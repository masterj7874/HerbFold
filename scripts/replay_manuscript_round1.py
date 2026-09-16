"""Replay revision figures into a new directory without rewriting archived inputs."""
from pathlib import Path
import argparse,json,sys,shutil,subprocess

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('analysis',choices=['figures','constraints'])
    p.add_argument('--output',type=Path,default=Path('tmp/round1-replay/figures'))
    a=p.parse_args();out=a.output.resolve()
    if not any(out.is_relative_to(ROOT/x) for x in ('tmp','reproduced')):
        raise ValueError('Choose a new output directory under this extraction\'s tmp/ or reproduced/.')
    if a.analysis=='constraints':
        out.mkdir(parents=True,exist_ok=True)
        source=ROOT/'research/manuscript/round1/constraints'
        for name in ['prior-campaign-selection-snapshot.json','reconstruction-500-input.json','reconstruction-selection-receipt.json']:
            shutil.copy2(source/name,out/name)
        result=subprocess.run([sys.executable,str(ROOT/'scripts/manuscript_round1_constraints.py'),'--output-dir',str(out),'--run-tests'],cwd=ROOT)
        raise SystemExit(result.returncode)
    manifest=ROOT/'PACKAGE_MANIFEST.json'
    old=Path(json.loads(manifest.read_text())['original_repository_root']) if manifest.exists() else ROOT
    import manuscript_round1_figures as module
    cls=module.journal.original.Figures;original=cls.track
    def track(self,value):
        v=Path(value)
        if v.is_absolute():
            if v.is_relative_to(ROOT):pass
            elif v.is_relative_to(old):v=ROOT/v.relative_to(old)
            else:raise ValueError('Input is outside the distributed source tree')
        return original(self,v)
    cls.track=track;module.OUT=out
    module.main()

if __name__=='__main__':main()
