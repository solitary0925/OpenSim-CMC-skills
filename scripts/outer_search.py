"""Bounded, baseline-first Fast/Slow search and batch quality reporting."""

from copy import deepcopy
import importlib.util
from math import sqrt
from pathlib import Path
import shutil
import statistics
import xml.etree.ElementTree as ET

import pandas as pd

from run_single_leg_batch import check_hashes, prepare, save, sha, solve, write_xml

JOINTS = ('ankle_angle', 'knee_angle', 'subtalar_angle')
FILES = ('single_leg_prescribed.osim', 'single_leg_motion.mot', 'target_plate.mot', 'target_loads.xml',
         'CMC_Setup.xml', 'CMC_Tasks.xml', 'CMC_ControlConstraints.xml')
CANDIDATES = {
    'fast': [('dt5_ankle144_24', .005, (144, 24), 1), ('dt2p5', .0025, None, 1)],
    'slow': [('dt6p25_cost16', .00625, None, .25), ('dt2p5_cost16', .0025, None, .25)],
}


def configure_strength(cfg, base):
    profile = cfg.get('muscle_strength_profile', 'prepared_so')
    if profile == 'prepared_so':
        return None
    if profile != 'rajagopal2016_fmax':
        raise ValueError('Expected prepared_so or rajagopal2016_fmax')
    donor = (base/cfg['rajagopal_model']).resolve(strict=True)
    helper = (base/cfg['so_strength_helper']).resolve(strict=True) if 'so_strength_helper' in cfg else (
        Path(__file__).resolve().parents[2]/'opensim-vicon-so/scripts/muscle_strength.py').resolve(strict=True)
    spec = importlib.util.spec_from_file_location('cmc_so_strength_helper', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare_strength_model, donor, {str(p):sha(p) for p in (donor, helper)}


def transfer(job, path, strength):
    function, donor, hashes = strength
    record = job['record']
    bilateral, = [Path(p) for p in record['source_sha256'] if Path(p).name == 'SO_model.osim']
    _, audit = function(bilateral, 'rajagopal2016_fmax', donor)
    side = record['side'].lower()
    mapping = {r['muscle']:r for r in audit['muscles'] if r['muscle'].endswith('_'+side)}
    file = path/'single_leg_prescribed.osim'
    model = ET.parse(file)
    muscles = {el.get('name'):el.find('max_isometric_force') for el in model.findall('.//Thelen2003Muscle')}
    if muscles.keys() != mapping.keys():
        raise ValueError('Rajagopal transfer inventory does not match the prepared target leg')
    for name, node in muscles.items():
        row = mapping[name]
        if float(node.text) != row['original_N']:
            raise ValueError(f'Prepared strength differs from original Gait2392 for {name}')
        node.text = str(row['selected_N'])
    write_xml(model, file)
    record['source_sha256'].update(hashes)
    audit.update(target_side=record['side'], muscles=list(mapping.values()),
                 transferred_count=sum(r['donor_muscle'] is not None for r in mapping.values()))
    audit['retained_count'] = len(mapping)-audit['transferred_count']
    save(path/'muscle_strength_audit.json', audit)


def configure(path, mode, dt, gains, scale, record):
    file = path/'CMC_Setup.xml'
    setup = ET.parse(file)
    tool = setup.find('CMCTool')
    tool.find('use_fast_optimization_target').text = str(mode == 'fast').lower()
    tool.find('cmc_time_window').text = str(dt)
    write_xml(setup, file)
    record['expected_native_window_s'][1] = record['tool_window_s'][1]-dt
    suffix = '_'+record['side'].lower()
    if gains:
        file = path/'CMC_Tasks.xml'
        tasks = ET.parse(file)
        task, = [el for el in tasks.findall('.//rdCMC_Joint') if el.findtext('coordinate').strip() == 'ankle_angle'+suffix]
        for key, value in zip(('kp','kv'), gains):
            node = task.find(key)
            node.text = ' '.join([str(value), *node.text.split()[1:]])
        write_xml(tasks, file)
    if scale != 1:
        model = ET.parse(path/'single_leg_prescribed.osim')
        constraints = ET.parse(path/'CMC_ControlConstraints.xml')
        for joint in JOINTS:
            name = 'reserve_'+joint+suffix
            actuator = model.find(f".//CoordinateActuator[@name='{name}']")
            control = constraints.find(f".//ControlLinear[@name='{name}.excitation']")
            if any(list(node) for node in control if node.tag in ('min_nodes','max_nodes')):
                raise ValueError(f'Time-varying reserve bounds for {name}')
            force = actuator.find('optimal_force')
            force.text = str(float(force.text)*scale)
            for tag in ('min','max'):
                for node in (actuator.find(tag+'_control'), control.find('default_'+tag)):
                    node.text = str(float(node.text)/scale)
        write_xml(model, path/'single_leg_prescribed.osim')
        write_xml(constraints, path/'CMC_ControlConstraints.xml')


def grade(result, side):
    suffix = '_'+side.lower()
    tracking = {r['coordinate']:r for r in result['tracking']}
    reserves = {r['coordinate']:r for r in result['reserves']}
    ankle = tracking['ankle_angle'+suffix]['RMS']
    joints = [reserves[joint+suffix] for joint in JOINTS]
    quantified = [r['CMC_percent_ID']/10 for r in joints if r['CMC_percent_ID'] is not None]
    return dict(gate=len(quantified)==len(JOINTS) and not (result['tracking_flags'] or result['reserve_flags']),
                vector=[ankle, *[r['CMC_peak_Nm'] for r in joints]], score=max([ankle, *quantified]))


def attempt(job, seed, path, cli, timeout, mode, name, dt, gains, scale):
    path.mkdir(parents=True, exist_ok=False)
    copied = deepcopy(job)
    record = copied['record']
    settings = dict(mode=mode, name=name, prediction_s=dt, ankle_gains=gains, reserve_cost_multiplier=1/scale**2)
    try:
        for f in FILES:
            shutil.copy2(seed/f, path/f)
        shutil.copytree(seed/'Geometry', path/'Geometry')
        if (seed/'muscle_strength_audit.json').is_file():
            shutil.copy2(seed/'muscle_strength_audit.json', path/'muscle_strength_audit.json')
        configure(path, mode, dt, gains, scale, record)
        record.update(controller=settings, prepared_model_sha256=sha(path/'single_leg_prescribed.osim'),
                      prepared_input_sha256={f:sha(path/f) for f in FILES})
        save(path/'input_audit.json', record)
        result = solve(copied, path, path/'CMC_Setup.xml', cli, timeout)
        return {**result, **settings, **grade(result, record['side'])}
    except Exception as error:
        result = dict(status='failed', output=str(path), reason=repr(error), **settings)
        save(path/'run_status.json', result)
        return result


def run_mode(job, seed, folder, cli, timeout, mode):
    def run(name, dt, gains, scale):
        return attempt(job, seed, folder/mode/name, cli, timeout, mode, name, dt, gains, scale)
    baseline = run('baseline', .01, None, 1)
    rows = [baseline]
    if baseline.get('gate'):
        return dict(quality='qualified_baseline', selected=baseline, attempts=rows)
    rows.extend(run(*config) for config in CANDIDATES[mode])
    reference = baseline.get('vector')
    usable = [r for r in rows if r['status']=='completed']
    for row in usable:
        row['joint_improvement'] = reference is not None and row['vector'][0]<reference[0]-1e-8 and all(
            a<=b+1e-8 for a,b in zip(row['vector'][1:],reference[1:]))
        if row is not baseline and reference is not None:
            assert row['common_frames']==baseline['common_frames'] and row['common_samples']==baseline['common_samples']
            assert row['common_SO_missing_frames']==baseline['common_SO_missing_frames']
    qualified = [r for r in usable if r['gate'] and (r['joint_improvement'] or reference is None)]
    improved = [r for r in usable if r['joint_improvement']]
    pool = qualified or improved or usable
    pick = min(pool, key=lambda r:(r['score'],r['vector'][0],r['name'])) if pool else None
    quality = ('qualified_recovery' if reference is None else 'qualified_candidate') if qualified else (
        'diagnostic' if pick else 'no_output')
    return dict(quality=quality, selected=pick, attempts=rows)


def execute_search(job, out, templates, cli, timeout, strength):
    record = job['record']
    folder = out/'cases'/record['trial']/record['stance']
    seed = folder/'prepared'
    prepare(job, seed, templates)
    if strength:
        transfer(job, seed, strength)
    check_hashes(record['source_sha256'])
    record['prepared_model_sha256'] = sha(seed/'single_leg_prescribed.osim')
    save(seed/'input_audit.json', record)
    modes = {mode:run_mode(job, seed, folder, cli, timeout, mode) for mode in CANDIDATES}
    eligible = [v for v in modes.values() if v['quality'].startswith('qualified')]
    if eligible:
        winner = next((v for v in eligible if v['quality']=='qualified_baseline'), eligible[0])
    else:
        winner = min((v for v in modes.values() if v['selected']),
                     key=lambda v:v['selected']['score'], default=None)
    selected = winner['selected'] if winner else None
    quality = 'qualified' if eligible else 'diagnostic' if selected else 'no_output'
    status = dict(status='completed' if selected else 'failed', output=str(folder), quality=quality,
                  selected=selected, modes=modes)
    save(folder/'case_status.json', status)
    # The rolling batch status holds indices. Per-case files hold every attempt and diagnostic.
    return dict(status=status['status'], output=str(folder), quality=quality,
                selected_mode=selected['mode'] if selected else None,
                selected_name=selected['name'] if selected else None)


def write_batch_metrics(out, rows):
    from run_single_leg_batch import read
    attempts = []
    details = []
    for row in rows:
        path = Path(row.get('output',''))/'case_status.json'
        if 'output' not in row or not path.is_file():
            continue
        case = read(path)
        identity = {key:row[key] for key in ('trial','stance','side','plate')}
        for mode, branch in case['modes'].items():
            for a in branch['attempts']:
                chosen = a['output']==(branch['selected'] or {}).get('output')
                primary = a['output']==(case['selected'] or {}).get('output')
                tags = dict(**identity, mode=mode, attempt=a['name'], quality=branch['quality'] if chosen else '',
                            is_baseline=a['name']=='baseline', mode_selected=chosen, primary=primary,
                            primary_quality=case['quality'] if primary else '', output=a['output'])
                attempts.append(dict(**tags, status=a['status'], reason=a.get('reason',''), gate=a.get('gate'),
                                     joint_improvement=a.get('joint_improvement'), frames=a.get('common_frames'),
                                     samples=a.get('common_samples'), score=a.get('score'),
                                     tracking_flags=[x['coordinate'] for x in a.get('tracking_flags',[])],
                                     reserve_flags=[x['coordinate'] for x in a.get('reserve_flags',[])]))
                if a['status']!='completed':
                    continue
                for t in a['tracking']:
                    details.append(dict(**tags, metric='tracking', unit='deg', coordinate=t['coordinate'],
                                        RMS=t['RMS'], MAX=t['peak'], reserve_percent=None))
                for r in a['reserves']:
                    details.append(dict(**tags, metric='reserve', unit='N·m', coordinate=r['coordinate'],
                                        RMS=r['CMC_RMS_Nm'], MAX=r['CMC_peak_Nm'], reserve_percent=r['CMC_percent_ID']))
    pd.DataFrame(attempts).to_csv(out/'attempt_index.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(details).to_csv(out/'per_stance_RMS_MAX.csv', index=False, encoding='utf-8-sig')
    groups = {}
    for d in details:
        sets = ['all_completed_attempts']
        if d['is_baseline']:
            sets.append(d['mode']+'_baseline')
        if d['mode_selected']:
            sets.extend((d['mode']+'_selected', d['mode']+'_selected_'+d['quality']))
        if d['primary']:
            sets.extend(('all_primary', 'primary_'+d['primary_quality']))
        coord = d['coordinate'].rsplit('_',1)[0]
        for label in sets:
            groups.setdefault((label,d['metric'],d['unit'],coord), []).append(d)
    aggregates = []
    for (group,metric,unit,coordinate), values in sorted(groups.items()):
        ratio = [x['reserve_percent'] for x in values if x['reserve_percent'] is not None]
        aggregates.append(dict(group=group,metric=metric,unit=unit,coordinate=coordinate,count=len(values),
                               mean_RMS=statistics.mean(x['RMS'] for x in values),
                               mean_MAX=statistics.mean(x['MAX'] for x in values),
                               overall_MAX=max(x['MAX'] for x in values),
                               mean_reserve_percent_ID=statistics.mean(ratio) if ratio else None))
    save(out/'batch_RMS_MAX.json', aggregates)
    pd.DataFrame(aggregates).to_csv(out/'batch_RMS_MAX.csv', index=False, encoding='utf-8-sig')
