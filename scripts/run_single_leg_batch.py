"""Run OpenSim 4.5 single-leg CMC from verified prescribed-pelvis SO handoffs."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

import numpy as np
import opensim as osim
import pandas as pd
from input_filter6 import choice, verify_receipt, storage as filter_storage


PREFIX = 'SingleLeg_CMC'
WARMUP = .030
DT = .010
TOL = 1e-8
PELVIS = {'pelvis_tx', 'pelvis_ty', 'pelvis_tz', 'pelvis_tilt', 'pelvis_list', 'pelvis_rotation'}
TRANSLATIONS = {'pelvis_tx', 'pelvis_ty', 'pelvis_tz'}
CALF = ('soleus', 'med_gas', 'lat_gas', 'tib_ant', 'tib_post', 'per_long')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_hashes(protected, cache=None):
    cache = {} if cache is None else cache
    for p, expected in protected.items():
        if p not in cache:
            cache[p] = sha(p)
        if cache[p] != expected:
            raise ValueError(f'Input checksum mismatch: {p}')


def table(path):
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    k = next(i for i, line in enumerate(lines) if line.strip().lower() == 'endheader')
    names = lines[k+1].split()
    values = np.loadtxt(lines[k+2:], ndmin=2)
    if values.shape[1] != len(names) or not np.isfinite(values).all() or (np.diff(values[:, 0]) < -1e-12).any():
        raise ValueError(f'Invalid storage time or data: {path}')
    # Keep the last report at a duplicate native integrator timestamp.
    _, indices = np.unique(values[::-1, 0], return_index=True)
    return names, values[len(values)-1-indices], any('indegrees=yes' in s.replace(' ', '').lower() for s in lines[:k])


def signal(columns, data, name, times):
    return np.interp(times, data[:, 0], data[:, columns.index(name)])


def verify_rra_handoff(audit, source, model, motion_columns, motion_data, paths, case, cache, filter_record=None):
    rra = audit.get('rra_source', {})
    if any(rra.get(k) != case[k] for k in ('trial', 'stance', 'side', 'plate')):
        raise ValueError('RRA handoff trial, stance, side or plate does not match the CMC case')
    inputs = {key: (source/rra[key]).resolve(strict=True)
              for key in ('model', 'motion', 'final_review', 'frame_verification')}
    checksums = audit['source_hashes']
    required = [*inputs.values(), *([paths['id_reference']] if 'id_reference' in paths else [])]
    if missing := [str(file) for file in required if str(file) not in checksums]:
        raise ValueError(f'RRA handoff has no recorded SHA-256 for: {missing}')
    check_hashes({str(file):checksums[str(file)] for file in required}, cache)
    review = read(inputs['final_review'])
    frame = read(inputs['frame_verification'])
    if (review.get('status') not in ('selected', 'selected_with_review_warnings')
            or review.get('selected_iteration') is None or not review.get('force_qualified')
            or frame.get('original_frame') is not True or frame.get('selected_under_force_rule') is not True):
        raise ValueError('RRA requires a force-qualified selected replay exported in the original frame')
    if review['status']=='selected_with_review_warnings' and not rra.get('warning_disposition', '').strip():
        raise ValueError('Record the user’s disposition of RRA review warnings before using this replay')
    columns, values, degrees = table(inputs['motion'])
    start, end = frame['window_native_s']
    filtered = (filter_record or {}).get('signals',{}).get('kinematics')
    if filtered:
        original = filtered['input']
        p = Path(original['path'])
        if checksums.get(str(p.resolve()))!=original['sha256'] or sha(p)!=original['sha256']:
            raise ValueError('Prefilter RRA motion must be protected by the prepared audit')
        motion_columns,motion_data,original_units = filter_storage(p)
        if original_units not in ('yes','true'):
            raise ValueError('Prefilter RRA motion must declare degrees')
    times = motion_data[:, 0]
    if not degrees or times[0]<max(start, values[0, 0])-TOL or times[-1]>min(end, values[-1, 0])+TOL:
        raise ValueError('Prepared motion extends outside the verified original-frame RRA replay')
    target = [f'{joint}_{case["side"].lower()}' for joint in
              ('hip_flexion', 'hip_adduction', 'hip_rotation', 'knee_angle', 'ankle_angle', 'subtalar_angle')]
    for name in PELVIS.union(target):
        if (name not in columns or name not in motion_columns
                or not np.allclose(motion_data[:,motion_columns.index(name)], signal(columns, values, name, times),
                                   atol=1e-6, rtol=0)):
            raise ValueError(f'Prepared motion does not reproduce the RRA replay coordinate {name}')
    body_path = './Model/BodySet/objects/Body'
    replay_bodies = {body.get('name'):body for body in ET.parse(inputs['model']).findall(body_path)}
    for body in ET.parse(model).findall(body_path):
        original = replay_bodies.get(body.get('name'))
        if original is None or any(not np.allclose(np.fromstring(body.findtext(tag), sep=' '),
                                                   np.fromstring(original.findtext(tag), sep=' '), rtol=1e-9, atol=1e-10)
                                   for tag in ('mass', 'mass_center', 'inertia')):
            raise ValueError(f'Prepared model does not preserve RRA inertial parameters for {body.get("name")}')
    return {**rra, **{key:str(file) for key,file in inputs.items()},
            'selected_iteration':review['selected_iteration'], 'window_native_s':[start, end]}


def verify_filtered_input(output, names, data):
    columns, values, _ = filter_storage(output['path'])
    times = data[:,0]
    if times[0]<values[0,0]-TOL or times[-1]>values[-1,0]+TOL or any(n not in columns for n in names):
        raise ValueError('Prepared file lacks matching filtered input coverage or columns')
    for i,name in enumerate(names[1:],1):
        if not np.allclose(data[:,i],signal(columns,values,name,times),rtol=1e-9,atol=1e-7):
            raise ValueError(f'Prepared input differs from the filtered source for {name}')


def preflight(case, base, cache, kinematics_source, filtering_decision=None):
    paths = {k: (base/case[k]).resolve(strict=True) for k in ('contact_audit', 'full_wrenches')}
    source = prepared_folder(case, base).resolve(strict=True)
    id_path = (base/case['id_reference']).resolve() if case.get('id_reference') else None
    if id_path is not None and id_path.is_file():
        paths['id_reference'] = id_path
    prep, audit = (read(source/n) for n in ('single_leg_prepared.json', 'input_audit.json'))
    if audit.get('kinematics_source', 'IK') != kinematics_source or (
            kinematics_source == 'IK' and 'rra_source' in audit):
        raise ValueError(f'Prepared handoff does not match the confirmed {kinematics_source} input')
    side, plate = case['side'], case['plate']
    trial, stance = case['trial'], case['stance']
    assert side in ('L', 'R') and prep['side'] == side and prep['plate'] == plate
    assert audit['target_contact_verified'] and audit['selection']['trial'] == trial and audit['selection']['stance'] == stance
    assert audit['selection']['side'] == side and audit['selection']['plate'] == plate
    model, motion, loads = [Path(prep[k]).resolve(strict=True) for k in ('model', 'motion', 'loads')]
    assert all(p.parent == source for p in (model, motion, loads))
    tree = ET.parse(loads)
    (ext,) = tree.findall('.//ExternalForce')
    assert ext.findtext('applied_to_body') == 'calcn_' + side.lower()
    force_path = (loads.parent/tree.findtext('.//datafile')).resolve(strict=True)
    assert force_path.parent == source
    fn, f, _ = table(force_path)
    target_fz = f'ground_force_{plate}_vy'
    assert target_fz in fn and len(fn) == 10 and all(f'_{plate}_' in n for n in fn[1:])
    for key, prefix in (('force', f'ground_force_{plate}_v'), ('point', f'ground_force_{plate}_p'), ('torque', f'ground_torque_{plate}_')):
        assert ext.findtext(key+'_identifier') == prefix and all(prefix+x in fn for x in 'xyz')
    events = read(paths['contact_audit'])['phases']
    target_event = audit['target_20N_window_s']
    event, = [x for x in events if x['side'] == side and x['plate'] == plate and np.allclose(x['target_window_s'], target_event, atol=TOL, rtol=0)]
    witness = event['contact_witnesses'][side]
    assert not witness['user_swing_assumption'] and not event['event']['censored_start'] and not event['event']['censored_end']
    rate, origin, first_frame = (audit[k] for k in ('marker_rate_hz','original_time_origin_s','original_first_frame'))
    assert rate > 0 and rate == event['point_rate_hz'] and first_frame == event['source_first_original_frame']
    rn, raw, _ = table(paths['full_wrenches'])
    raw_fz = raw[:, rn.index(target_fz)]
    mn, motion_table, degrees = table(motion)
    if not degrees:
        raise ValueError('Prepared motion must declare rotational coordinates in degrees')
    candidates = []
    for interval in witness['complete_intervals']:
        lo = max(target_event[0],interval['start_s'],f[0,0],raw[0,0],motion_table[0,0])
        hi = min(target_event[1],interval['end_s'],f[-1,0],raw[-1,0],motion_table[-1,0])
        if hi > lo:
            candidates.append((lo,hi,interval))
    if not candidates:
        raise ValueError('No continuous verified support with this motion and target load')
    lo,hi,interval = max(candidates,key=lambda item:(item[1]-item[0],-item[0]))
    t = original_grid(lo,hi,origin,rate)
    if len(t)<3:
        raise ValueError('Fewer than three frames in the verified CMC input window')
    first, last = float(t[0]), float(t[-1])
    raw_support = (raw[:,0]>=lo-TOL)&(raw[:,0]<=hi+TOL)
    assert raw_support.any() and (raw_fz[raw_support]>=19.99).all()
    warm_start = round(first-WARMUP, 9)
    precontact = (raw[:,0]>=warm_start-TOL)&(raw[:,0]<target_event[0]-TOL)
    context_verified = (interval['start_s']<=warm_start+TOL and f[0,0]<=warm_start and raw[0,0]<=warm_start
                        and motion_table[0,0]<=warm_start+TOL
                        and (raw_fz[precontact]<20.01).all())
    start = warm_start if context_verified else first
    # The final time includes only verified prediction context. Its last force sample bounds the native solve.
    context_end = min(interval['end_s'],f[-1,0],raw[-1,0],motion_table[-1,0],last+DT)
    final = float(f[f[:,0]<=context_end+TOL,0][-1])
    eligible = t[(t>=start+WARMUP-TOL)&(t<=final-DT+TOL)]
    if len(eligible)<3:
        raise ValueError('Fewer than three CMC frames after initialization and prediction')
    frames = first_frame + np.rint((t-origin)*rate).astype(int)
    checked = dict(audit['source_hashes'])
    provenance = [model,motion,loads,force_path,*paths.values(),source/'input_audit.json',source/'single_leg_prepared.json']
    # Legacy handoffs can include upstream comparison outputs. They are not CMC inputs.
    checked = {p:h for p,h in checked.items() if not is_solver_result(p)}
    assert str(paths['contact_audit']) in checked and str(paths['full_wrenches']) in checked
    for path in provenance:
        if str(path) not in checked:
            checked[str(path)] = sha(path)
    check_hashes(checked, cache)
    selected = choice(filtering_decision) if filtering_decision is not None else set()
    filter_record = None
    if selected:
        receipt = (source/audit['input_filtering_record']).resolve(strict=True)
        if checked.get(str(receipt))!=sha(receipt):
            raise ValueError('Filter receipt must be protected by the prepared source audit')
        filter_record = verify_receipt(receipt,filtering_decision,checked)
        if 'kinematics' in selected:
            verify_filtered_input(filter_record['signals']['kinematics']['outputs']['motion'],mn,motion_table)
        if 'wrench' in selected:
            verify_filtered_input(filter_record['signals']['wrench']['outputs']['wrench'],fn,f)
        if id_path is not None and id_path.is_file() and str(id_path) not in audit['source_hashes']:
            raise ValueError('An ID reference for filtered inputs needs matching provenance')
    rra = verify_rra_handoff(audit,source,model,mn,motion_table,paths,case,cache,filter_record) if kinematics_source=='RRA' else None
    record = dict(trial=trial, stance=stance, side=side, plate=plate, prepared_folder=str(source),
                  kinematics_source=kinematics_source,
                  filtering_decision=filtering_decision,input_filtering=filter_record,
                  original_20N_window_s=target_event, input_support_frames=[int(frames[0]),int(frames[-1])],
                  verified_input_window_s=[lo,hi], support_times=t.tolist(),
                  tool_window_s=[start,final], expected_native_window_s=[start+WARMUP,final-DT],
                  before_window_initialization=bool(context_verified), selection_times=eligible.tolist(),
                  id_reference_requested=str(id_path) if id_path is not None else None,
                  original_first_frame=first_frame, time_origin_s=origin, point_rate_hz=rate, source_sha256=checked)
    if rra is not None:
        record['rra_source'] = rra
    return dict(record=record, model=model, motion=motion, loads=tree,
                fn=fn, force=f[f[:,0]<=final+TOL], id_reference=paths.get('id_reference'))


def prepared_folder(case, base):
    return base/(case['prepared_folder'] if 'prepared_folder' in case else case['so_folder'])


def is_solver_result(path):
    name = Path(path).name.casefold()
    return name in {'validated_muscle_curves.csv','quality.json'} or '_staticoptimization_' in name


def original_grid(lo,hi,origin,rate):
    bounds = (np.asarray([lo-TOL,hi+TOL])-origin)*rate
    return np.round(origin+np.arange(int(np.ceil(bounds[0])),int(np.floor(bounds[1]))+1)/rate,9)


def write_xml(tree, path):
    ET.indent(tree)
    tree.write(path, encoding='utf-8', xml_declaration=True)


def prepare(job, out, templates):
    r = job['record']
    model = ET.parse(job['model'])
    (out/'Geometry').mkdir(parents=True)
    for el in model.iter('mesh_file'):
        mesh = (job['model'].parent/el.text.strip()).resolve()
        if not mesh.is_file():
            mesh = job['model'].parent/'Geometry'/mesh.name
        if not mesh.is_file():
            raise FileNotFoundError(mesh)
        r['source_sha256'][str(mesh)] = sha(mesh)
        shutil.copy2(mesh, out/'Geometry'/mesh.name)
        el.text = mesh.name
    write_xml(model, out/'single_leg_prescribed.osim')
    shutil.copy2(job['motion'], out/'single_leg_motion.mot')
    with (out/'target_plate.mot').open('w',encoding='utf-8') as f:
        f.write(f'TargetPlate\nnRows={len(job["force"])}\nnColumns={len(job["fn"])}\ninDegrees=no\nendheader\n')
        f.write('\t'.join(job['fn'])+'\n')
        np.savetxt(f,job['force'],fmt='%.15g',delimiter='\t')
    job['loads'].find('.//datafile').text='target_plate.mot'
    write_xml(job['loads'],out/'target_loads.xml')
    m=osim.Model(str(out/'single_leg_prescribed.osim')); s=m.initSystem(); cs=m.getCoordinateSet()
    side=r['side'].lower()
    active={cs.get(i).getName() for i in range(cs.getSize()) if not cs.get(i).isConstrained(s)}
    expect={name+'_'+side for name in ('hip_flexion','hip_adduction','hip_rotation','knee_angle','ankle_angle','subtalar_angle')}
    assert active==expect and all(cs.get(n).isPrescribed(s) for n in PELVIS)
    if r.get('kinematics_source')=='RRA' or 'kinematics' in (r.get('filtering_decision') or {}).get('signals',[]):
        motion = osim.Storage(str(out/'single_leg_motion.mot'))
        m.getSimbodyEngine().convertDegreesToRadians(motion)
        splines = osim.GCVSplineSet(5, motion)
        for name in PELVIS:
            prescribed = cs.get(name).getPrescribedFunction()
            reference = splines.getGCVSpline(splines.getIndex(name))
            discrepancy = max(abs(prescribed.calcValue(osim.Vector(1,t))-reference.calcValue(osim.Vector(1,t)))
                              for t in r['selection_times'])
            if discrepancy>1e-5:
                raise ValueError(f'Prescribed pelvis does not match the prepared motion for {name}: {discrepancy:g}')
    muscles={m.getMuscles().get(i).getName() for i in range(m.getMuscles().getSize())}
    assert len(muscles)==46 and all(n.endswith('_'+side) for n in muscles)
    reserves={m.getForceSet().get(i).getName() for i in range(m.getForceSet().getSize())}-muscles
    assert reserves=={'reserve_'+n for n in active}
    tasks=ET.parse(templates/'gait2392_CMC_Tasks.xml'); objects=tasks.find('./rdCMC_TaskSet/objects')
    for el in list(objects):
        if el.findtext('coordinate').strip() not in active:
            objects.remove(el)
    assert {el.findtext('coordinate').strip() for el in objects}==active
    write_xml(tasks,out/'CMC_Tasks.xml')
    constraints=ET.parse(templates/'gait2392_CMC_ControlConstraints.xml'); objects=constraints.find('./ControlSet/objects')
    for el in list(objects):
        n=el.get('name').removesuffix('.excitation')
        if n in muscles:
            continue
        if n.endswith('_reserve') and n.removesuffix('_reserve') in active:
            el.set('name','reserve_'+n.removesuffix('_reserve')+'.excitation')
        else:
            objects.remove(el)
    assert {el.get('name') for el in objects}=={n+'.excitation' for n in muscles|reserves}
    write_xml(constraints,out/'CMC_ControlConstraints.xml')
    setup=ET.parse(templates/'subject01_Setup_CMC.xml'); tool=setup.find('CMCTool'); tool.set('name',PREFIX)
    for key,value in dict(model_file='single_leg_prescribed.osim',force_set_files='',results_directory='results',
                          initial_time=r['tool_window_s'][0],final_time=r['tool_window_s'][1],external_loads_file='target_loads.xml',
                          desired_kinematics_file='single_leg_motion.mot',task_set_file='CMC_Tasks.xml',
                          constraints_file='CMC_ControlConstraints.xml',lowpass_cutoff_frequency=-1,cmc_time_window=DT).items():
        tool.find(key).text=str(value)
    for el in tool.findall('./AnalysisSet/objects/*/step_interval'):
        el.text='1'
    write_xml(setup,out/'CMC_Setup.xml')
    r.update(active_coordinates=sorted(active),muscles=sorted(muscles),prepared_model_sha256=sha(out/'single_leg_prescribed.osim'))
    save(out/'input_audit.json',r)
    return out/'CMC_Setup.xml'


def review(job, out, log):
    r=job['record']; native=out/'results'
    sc,states,_=table(native/f'{PREFIX}_states.sto')
    fc,force,_=table(native/f'{PREFIX}_Actuation_force.sto')
    qc,q,deg=table(native/f'{PREFIX}_Kinematics_q.sto')
    table(native/f'{PREFIX}_controls.sto')
    assert (native/f'{PREFIX}_controls.xml').stat().st_size>0
    ic,ik,ikdeg=table(out/'single_leg_motion.mot')
    start,end=states[[0,-1],0]
    assert deg and ikdeg and np.allclose([start,end],r['expected_native_window_s'],atol=1e-6,rtol=0)
    assert all(np.allclose(a[[0,-1],0],[start,end],atol=1e-6,rtol=0) for a in (q,force))
    grid=np.asarray(r['support_times'])
    times=grid[(grid>=start-1e-6)&(grid<=end+1e-6)]
    if len(times)<3:
        raise ValueError('Fewer than three CMC evaluation frames')
    frames=r['original_first_frame']+np.rint((times-r['time_origin_s'])*r['point_rate_hz']).astype(int)
    lo,hi=r['original_20N_window_s']; phase=100*(times-lo)/(hi-lo)
    curves=[]; muscles=[]
    peak=lambda x:float(np.max(np.abs(x)))
    rms=lambda x:float(np.sqrt(np.mean(x*x)))
    for name in r['muscles']:
        label,=[x for x in sc if x.endswith('/'+name+'/activation')]
        ca=signal(sc,states,label,times); cf=signal(fc,force,name,times)
        for t,f,p,a,b in zip(times,frames,phase,ca,cf):
            curves.append(dict(time_s=t,original_frame=f,original_stance_percent=p,muscle=name,
                               CMC_activation=a,CMC_force_N=b))
        muscles.append(dict(muscle=name,CMC_peak_N=float(cf.max()),CMC_peak_activation_percent=float(100*ca.max()),
                            CMC_99p99_samples=int(np.sum(ca>=.9999))))

    id_columns,id_values = [],None
    id_status = 'unavailable'
    if job['id_reference'] is not None:
        try:
            id_columns,id_values,_ = table(job['id_reference'])
            id_status = 'available' if np.isin(times.round(8),id_values[:,0].round(8)).all() else 'incomplete_coverage'
            if id_status=='available' and any(name+'_moment' not in id_columns for name in r['active_coordinates']):
                id_status = 'incomplete_columns'
        except (OSError,ValueError,StopIteration):
            id_status = 'invalid_storage'

    def joint_metrics(samples):
        tracking,reserves = [],[]
        sample_frames = r['original_first_frame']+np.rint((samples-r['time_origin_s'])*r['point_rate_hz']).astype(int)
        for name in r['active_coordinates']:
            d=signal(qc,q,name,samples)-signal(ic,ik,name,samples)
            tracking.append(dict(coordinate=name,unit='deg',RMS=rms(d),peak=peak(d)))
            x=signal(fc,force,'reserve_'+name,samples)
            key = name+'_moment'
            ref = peak(signal(id_columns,id_values,key,samples)) if id_status=='available' and key in id_columns else None
            reserves.append(dict(coordinate=name,CMC_peak_Nm=peak(x),CMC_RMS_Nm=rms(x),ID_peak_Nm=ref,
                                 CMC_percent_ID=100*peak(x)/ref if ref is not None and ref>1 else None,
                                 CMC_peak_frame=int(sample_frames[np.argmax(abs(x))])))
        return tracking,reserves

    track,reserves = joint_metrics(times)
    selection_times = np.asarray(r['selection_times'])
    assert np.isin(selection_times,times).all()
    selection_tracking,selection_reserves = joint_metrics(selection_times)
    m=osim.Model(str(out/'single_leg_prescribed.osim')); cs=m.getCoordinateSet(); pelvis={}
    for name in sorted(PELVIS):
        f=cs.get(name).getPrescribedFunction()
        ref=np.array([f.calcValue(osim.Vector(1,float(t))) for t in q[:,0]])
        is_translation=name in TRANSLATIONS
        diff=(q[:,qc.index(name)]-ref)*1000 if is_translation else q[:,qc.index(name)]-np.rad2deg(ref)
        pelvis[name]=peak(diff)
        assert pelvis[name] < (.1 if is_translation else .01), name
    for name,rows in [('cmc_curves.csv',curves),('muscle_summary.csv',muscles),('reserve_comparison.csv',reserves),('tracking_errors.csv',track)]:
        pd.DataFrame(rows).to_csv(out/name,index=False,encoding='utf-8-sig')
    ratio_flags=[x for x in reserves if x['CMC_percent_ID'] is not None and x['CMC_percent_ID']>=10]
    track_flags=[x for x in track if x['RMS']>(1 if x['coordinate'].startswith(('ankle','subtalar')) else 2)]
    saturated=[x['muscle'] for x in muscles if x['CMC_99p99_samples']]
    summary=dict(kinematics_source=r.get('kinematics_source','IK'),native_window_s=[float(start),float(end)],evaluation_samples=len(times),
                 evaluation_frames=[int(frames[0]),int(frames[-1])],evaluation_phase_percent=[float(phase[0]),float(phase[-1])],
                 selection_times=selection_times.tolist(),selection_tracking=selection_tracking,selection_reserves=selection_reserves,
                 id_reference_status=id_status,CMC_saturated_muscles=saturated,
                 reserve_flags=ratio_flags,tracking_flags=track_flags,pelvis_prescription_peak=pelvis,
                 reserves=reserves,tracking=track,minimum_excitation_resets=log.count('has been reset to 0.02'),
                 six_muscles=[x for x in muscles if x['muscle'][:-2] in CALF])
    save(out/'comparison.json',summary)
    return summary


def execute(job, out, template, cli, timeout):
    r=job['record']; path=out/'cases'/r['trial']/r['stance']
    setup=prepare(job,path,template)
    return solve(job, path, setup, cli, timeout)


def solve(job, path, setup, cli, timeout):
    r=job['record']
    env=os.environ.copy();env['PATH']=str(cli.parent)+os.pathsep+env.get('PATH','')
    began=time.monotonic()
    with (path/'CMC_console.log').open('w',encoding='utf-8') as log_file:
        with subprocess.Popen([str(cli),'run-tool',setup.name],cwd=path,env=env,stdout=log_file,stderr=subprocess.STDOUT) as proc:
            save(path/'run_status.json',dict(status='running',pid=proc.pid))
            try:
                code=proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait()
                raise RuntimeError(f'Native CMC timed out at {timeout} s')
    log=(path/'CMC_console.log').read_text(encoding='utf-8',errors='replace')
    if code or 'Finished tracking the specified kinematics.' not in log or re.search(r'\[error\]|OPTIMIZATION FAILED',log,re.I):
        msg=' | '.join(line.strip() for line in log.splitlines()[-14:] if line.strip())
        raise RuntimeError(f'CMC stopped (exit {code}): {msg}')
    check_hashes(r['source_sha256'])
    result=review(job,path,log)
    status=dict(status='completed',seconds=time.monotonic()-began,output=str(path),**result)
    save(path/'run_status.json',status)
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('config',type=Path)
    parser.add_argument('--check-only',action='store_true');args=parser.parse_args()
    base=args.config.resolve().parent;cfg=read(args.config)
    kinematics_source = cfg.get('kinematics_source')
    if kinematics_source not in ('IK','RRA'):
        parser.error('Set kinematics_source to the user-confirmed input: IK or RRA')
    filtering_decision = cfg.get('filtering_decision')
    if 'filtering_decision' in cfg:
        choice(filtering_decision)
    outer = cfg.get('outer_search', False)
    if outer:
        from export_quality_dashboard import check_dashboard_runtime, export_dashboard
        from outer_search import execute_search, configure_strength, write_batch_metrics
        strength = configure_strength(cfg, base)
        dashboard_runtime = check_dashboard_runtime()
    out=(base/cfg['output']).resolve();cli=(base/cfg['opensim_cmd']).resolve(strict=True)
    templates=(base/cfg['gait2392_templates']).resolve(strict=True)
    assert '4.5' in subprocess.check_output([str(cli),'-V'],text=True) and '4.5' in osim.GetVersionAndDate()
    workers=cfg.get('workers',1);timeout=cfg.get('timeout_s',420)
    assert isinstance(workers,int) and 1<=workers<=int(os.environ.get('NUMBER_OF_PROCESSORS','1')) and timeout>0
    hashes={str(p):sha(p) for p in [templates/n for n in ('gait2392_CMC_Tasks.xml','gait2392_CMC_ControlConstraints.xml','subject01_Setup_CMC.xml')]}
    osim.Logger.removeFileSink();osim.Logger.setLevelString('error')
    exclusions=cfg.get('exclude_trial_prefixes',[])
    assert isinstance(exclusions,list) and all(isinstance(x,str) and x.strip() for x in exclusions)
    excluded_prefixes=tuple(x.casefold() for x in exclusions)
    jobs=[];rows=[];cache={};seen=set()
    for case in cfg['cases']:
        key=case['trial'],case['stance']
        identity=tuple(part.casefold() for part in key)
        assert identity not in seen and all(re.fullmatch(r'[A-Za-z0-9_-]+',part) for part in key)
        seen.add(identity)
        if identity[0].startswith(excluded_prefixes):
            rows.append(dict(trial=key[0],stance=key[1],status='excluded_by_config'));continue
        source=prepared_folder(case,base).resolve()
        if out==source or source in out.parents or out in source.parents:
            raise ValueError('Keep the CMC output tree separate from the prepared SO input tree')
        try:
            job=preflight(case,base,cache,kinematics_source,filtering_decision);job['record']['source_sha256'].update(hashes);jobs.append(job)
        except Exception as error:
            rows.append(dict(trial=key[0],stance=key[1],side=case['side'],status='blocked',reason=repr(error)))
    print('Preflight',len(jobs),'ready',[(r['trial'],r.get('reason')) for r in rows],flush=True)
    if args.check_only:
        return 2 if any(r['status']=='blocked' for r in rows) else 0
    out.mkdir(parents=True,exist_ok=False)
    save(out/'preflight.json',dict(kinematics_source=kinematics_source,filtering_decision=filtering_decision,
        ready=[{k:v for k,v in job['record'].items() if k!='source_sha256'} for job in jobs],not_started=rows))
    shutil.copy2(args.config,out/'requested_config.json')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        if outer:
            pending={pool.submit(execute_search,job,out,templates,cli,timeout,strength):job['record'] for job in jobs}
        else:
            pending={pool.submit(execute,job,out,templates,cli,timeout):job['record'] for job in jobs}
        for future in as_completed(pending):
            r=pending[future];ident={k:r[k] for k in ('trial','stance','side','plate','input_support_frames','before_window_initialization')}
            try:
                result=future.result()
            except Exception as error:
                path=out/'cases'/r['trial']/r['stance']
                result=dict(status='failed',reason=repr(error),output=str(path))
                if path.is_dir():
                    save(path/'run_status.json',result)
            rows.append({**ident,**result})
            save(out/'batch_status.json',dict(status='running',kinematics_source=kinematics_source,total=len(cfg['cases']),rows=rows))
            print(len(rows),len(cfg['cases']),r['trial'],r['stance'],result['status'],result.get('reason',''),flush=True)
    rows.sort(key=lambda r:(r['trial'].lower(),r['stance']))
    counts=dict(Counter(r['status'] for r in rows))
    final = dict(status='finished',kinematics_source=kinematics_source,filtering_decision=filtering_decision,total=len(cfg['cases']),counts=counts,rows=rows)
    if outer:
        final['quality_workbook'] = {'status':'pending'}
    save(out/'batch_status.json',final)
    export_failed = False
    if outer:
        write_batch_metrics(out, rows)
        try:
            dashboard = export_dashboard(out, dashboard_runtime)
            final['quality_workbook'] = {'status':'completed','output':str(dashboard)}
            print('CMC quality workbook', dashboard, flush=True)
        except (OSError, subprocess.SubprocessError) as error:
            export_failed = True
            final['quality_workbook'] = {'status':'failed','reason':str(error)}
            print('CMC quality workbook failed', error, flush=True)
        save(out/'batch_status.json',final)
    print('Finished',counts,flush=True)
    return 2 if counts.get('blocked') or counts.get('failed') or export_failed else 0


if __name__=='__main__':
    raise SystemExit(main())
