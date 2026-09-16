"""Publication-only S5 label spacing; preserve every original audit file byte."""
from pathlib import Path
import argparse,hashlib,json,shutil
import matplotlib.figure
import manuscript_round1_assays as assay

ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=ROOT/'output/manuscript/figures/round1')
    out=p.parse_args().output.resolve()
    allowed=[ROOT/'output/manuscript/figures/round1',ROOT/'tmp',ROOT/'reproduced']
    if not any(out==x or out.is_relative_to(x) for x in allowed):raise ValueError('Choose revision figure or separate replay directory')
    out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'research/manuscript/round1/assays'
    before={x:sha(x) for x in source.rglob('*') if x.is_file() and '__pycache__' not in x.parts}
    save=matplotlib.figure.Figure.savefig
    def polished(fig,path,*args,**kwargs):
        if len(fig.axes)>=3:
            fig.axes[2].set_xticks([.30,.40,.50,.60],['0.30','0.40','0.50','0.60'])
            for t in fig.axes[1].texts:
                if t.get_text().startswith('Width bootstrap:'):t.set_y(.72)
        return save(fig,out/Path(path).name,*args,**kwargs)
    matplotlib.figure.Figure.savefig=polished
    assay.write_csv=lambda *a,**kw:None
    assay.write_json=lambda *a,**kw:None
    assay.make_figure(source)
    assert all(sha(p)==h for p,h in before.items()),'Original assay artifact changed'
    shutil.copy2(source/'figure-S5-assay-audit.caption.txt',out/'figure-S5-assay-audit.caption.txt')
    receipt={'stage':'publication spacing only','source_bytes_unchanged':True,'original_files_checked':len(before),'data_changed':False,
      'script_sha256':sha(Path(__file__)),'files':[{'path':str(x.relative_to(ROOT)),'sha256':sha(x),'bytes':x.stat().st_size} for x in sorted(out.glob('figure-S5-assay-audit.*'))]}
    (out/'figure-S5-polish.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt))

if __name__=='__main__':main()
