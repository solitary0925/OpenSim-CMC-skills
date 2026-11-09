"""Prepare auditable 4th-order, 6 Hz zero-phase motion or load copies."""

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

PROFILE = 'butterworth_4_6hz_zero_phase'
NO_ADDED = 'no_added_filtering'
SIGNALS = {'kinematics','wrench'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def choice(decision):
    if (not isinstance(decision,dict) or decision.get('status')!='confirmed'
            or not str(decision.get('confirmation_source') or '').strip()):
        raise ValueError('Confirm no added filtering or 4th-order 6 Hz zero-phase Butterworth first')
    signal_names = decision.get('signals',[])
    if not isinstance(signal_names,list) or not all(isinstance(x,str) and x in SIGNALS for x in signal_names):
        raise ValueError('Use signals kinematics and/or wrench')
    selected = set(signal_names)
    if decision.get('choice')==NO_ADDED and not selected:
        return selected
    if decision.get('choice')==PROFILE and selected and len(selected)==len(signal_names):
        history = decision.get('upstream_filtering',{})
        if not isinstance(history,dict) or any(history.get(x)!='verified_unfiltered' for x in selected):
            raise ValueError('Verify every selected signal is unfiltered before adding this filter. Reuse an already filtered source without adding another pass.')
        return selected
    raise ValueError('Filtering choice and signal scope disagree')


def lowpass(times, values):
    from scipy import __version__ as version
    from scipy.signal import butter, sosfiltfilt

    t = np.asarray(times,dtype=float)
    x = np.asarray(values,dtype=float)
    if len(t)<=15 or len(x)!=len(t) or not np.isfinite(t).all() or not np.isfinite(x).all():
        raise ValueError('6 Hz filter requires more than 15 finite samples')
    steps = np.diff(t)
    step = float(np.median(steps))
    if not 0<step<1/12 or not np.allclose(steps,step,rtol=0,atol=1e-8):
        raise ValueError('6 Hz filter requires a uniform native time grid and a sampling rate above 12 Hz')
    sos = butter(4,6,fs=1/step,btype='lowpass',output='sos')
    result = sosfiltfilt(sos,x,axis=0,padtype='odd',padlen=15)
    if not np.isfinite(result).all():
        raise ValueError('Nonfinite filtered signal')
    return result,dict(sample_rate_hz=1/step,design_order=4,cutoff_hz=6,zero_phase=True,
                       padtype='odd',pad_samples=15,scipy_version=version,sos=sos.tolist())


def filter_motion(times, columns, values, translation_names, degrees):
    source = np.asarray(values,dtype=float)
    x = source.copy()
    linear = set(translation_names)
    if not linear<=set(columns) or len(set(columns))!=len(columns) or len(columns)!=x.shape[1]:
        raise ValueError('Coordinate list or explicit translation names do not match the motion')
    rotation = [i for i,name in enumerate(columns) if name not in linear]
    if rotation:
        radians = np.deg2rad(x[:,rotation]) if degrees else x[:,rotation]
        x[:,rotation] = np.unwrap(radians,axis=0)
    result,info = lowpass(times,x)
    if degrees and rotation:
        result[:,rotation] = np.rad2deg(result[:,rotation])
    constant = np.ptp(source,axis=0)==0
    result[:,constant] = source[:,constant]
    return result,info


def filter_wrench(times, force, point, torque):
    fixed_moment = torque + np.cross(point,force)
    filtered,info = lowpass(times,np.column_stack((force,fixed_moment)))
    f,m = np.split(filtered,2,axis=1)
    new_torque = m - np.cross(point,f)
    assert np.allclose(new_torque+np.cross(point,f),m,rtol=0,atol=1e-9)
    return f,new_torque,info


def storage(path):
    lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    end = next(i for i,line in enumerate(lines) if line.strip().casefold()=='endheader')
    names = lines[end+1].split()
    data = np.loadtxt(lines[end+2:],ndmin=2)
    if names[0]!='time' or len(set(names))!=len(names) or data.shape[1]!=len(names):
        raise ValueError(f'Invalid scalar OpenSim table {path}')
    degrees = [line.partition('=')[2].strip().casefold() for line in lines[:end]
               if line.partition('=')[0].strip().casefold()=='indegrees']
    return names,data,degrees[0] if degrees else None


def write_storage(path,names,data,degrees):
    with path.open('x',encoding='utf-8',newline='\n') as file:
        file.write(f'Butterworth6Hz\nversion=1\nnRows={len(data)}\nnColumns={len(names)}\ninDegrees={degrees}\nendheader\n')
        file.write('\t'.join(names)+'\n')
        np.savetxt(file,data,delimiter='\t',fmt='%.16g')


def load_columns(tree,names):
    used = set()
    for entry in tree.findall('.//ExternalForce'):
        if any(entry.findtext(tag)!='ground' for tag in ('force_expressed_in_body','point_expressed_in_body')):
            raise ValueError('Force and application point must be expressed in ground in SI units')
        groups = [[names.index(entry.findtext(tag)+axis) for axis in 'xyz']
                  for tag in ('force_identifier','point_identifier','torque_identifier')]
        flat = {x for group in groups for x in group}
        if len(flat)!=9 or used&flat:
            raise ValueError('External wrench columns overlap')
        used.update(flat)
        yield entry.get('name'),groups
    if not used:
        raise ValueError('No ExternalForce definition')


def output_record(path):
    return dict(path=str(path.resolve()),sha256=sha(path))


def verify_receipt(path, decision, protected):
    record = read(path)
    selected = choice(decision)
    if record.get('decision')!=decision or set(record.get('signals',{}))!=selected:
        raise ValueError('Filter receipt does not match the confirmed run decision')
    for name in selected:
        signal = record['signals'][name]
        if (signal.get('design_order'),signal.get('cutoff_hz'),signal.get('zero_phase')) != (4,6,True):
            raise ValueError('Receipt parameters do not match fourth-order 6 Hz zero-phase filtering')
        sources = [signal['input']] if name=='kinematics' else signal['inputs']
        if any(sha(item['path'])!=item['sha256'] for item in sources):
            raise ValueError('A prefilter source changed after the receipt was generated')
        required = ('motion',) if name=='kinematics' else ('loads','wrench')
        for key in required:
            item = signal['outputs'][key]
            output_path = str(Path(item['path']).resolve(strict=True))
            if sha(output_path)!=item['sha256'] or protected.get(output_path)!=item['sha256']:
                raise ValueError(f'Filtered {key} must be protected by the downstream input audit')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--decision',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--motion',type=Path)
    parser.add_argument('--loads',type=Path)
    parser.add_argument('--translations',nargs='*')
    args = parser.parse_args()
    decision = read(args.decision)
    selected = choice(decision)
    if not selected or ('kinematics' in selected)!=bool(args.motion) or ('wrench' in selected)!=bool(args.loads):
        parser.error('Provide a source file for each selected filter signal only')
    if args.motion and args.translations is None:
        parser.error('List all translational coordinates explicitly with --translations')
    out = args.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    record = dict(decision=decision,signals={})
    if args.motion:
        names,data,degrees = storage(args.motion)
        if degrees not in ('yes','no','true','false'):
            raise ValueError('Motion must declare inDegrees')
        filtered,info = filter_motion(data[:,0],names[1:],data[:,1:],args.translations,degrees in ('yes','true'))
        dst = out/'motion_filter6.mot'
        write_storage(dst,names,np.column_stack((data[:,0],filtered)),degrees)
        record['signals']['kinematics'] = dict(input=output_record(args.motion),outputs={'motion':output_record(dst)},**info)
    if args.loads:
        tree = ET.parse(args.loads)
        source = (args.loads.parent/tree.findtext('.//datafile')).resolve(strict=True)
        names,data,_ = storage(source)
        filtered = data.copy()
        for _,(f,p,m) in load_columns(tree,names):
            filtered[:,f],filtered[:,m],info = filter_wrench(data[:,0],data[:,f],data[:,p],data[:,m])
            # Preserve zeroed unloaded samples in contact-masked load tables.
            unloaded = (data[:,f]==0).all(axis=1) & (data[:,m]==0).all(axis=1)
            filtered[np.ix_(unloaded,f+m)] = 0
        dst,xml = out/'wrench_filter6.mot',out/'ExternalLoads.xml'
        write_storage(dst,names,filtered,'no')
        tree.find('.//datafile').text = dst.name
        tree.write(xml,encoding='utf-8',xml_declaration=True)
        record['signals']['wrench'] = dict(inputs=[output_record(source),output_record(args.loads)],
                                          outputs={'wrench':output_record(dst),'loads':output_record(xml)},**info)
    (out/'input_filtering.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
