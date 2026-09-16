"""Descriptive chemical-space comparison from frozen herb and ChEMBL records.

CPU analysis only. No molecular generation, activity model, or inference service.
The saved reference CSV permits replay without the local catalog database.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import rdkit
from rdkit import Chem, DataStructs
from rdkit.Chem import Descriptors, Crippen, Lipinski, rdMolDescriptors, rdFingerprintGenerator
from rdkit.Chem.Scaffolds import MurckoScaffold
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'research/manuscript/q1_extension/chemistry'
FIG = ROOT/'output/manuscript/figures'
HERBS = ['gegen','huangqin','huanglian','gancao']
NAMES = ['Gegen','Huangqin','Huanglian','Gancao']
COLORS = ['#183B56','#158A88','#C58B24','#A54678']
FEATURES = ['mw','logp','tpsa','hbd','hba','rotatable_bonds','fraction_csp3','rings']


def read_csv(p):
    with p.open(newline='') as f:return list(csv.DictReader(f))


def write_csv(name, rows):
    with (OUT/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True,exist_ok=True);FIG.mkdir(parents=True,exist_ok=True)
    source=ROOT/'research/manuscript/tables/gqd_provenance_records.csv'
    occurrence=read_csv(source)
    ref=OUT/'chembl_reference_snapshot.csv'
    if not ref.exists():
        con=sqlite3.connect(f'file:{ROOT / "runtime/discovery/discovery.sqlite3"}?mode=ro',uri=True)
        con.row_factory=sqlite3.Row
        con.execute('BEGIN')
        query="""SELECT c.id compound_id,c.canonical_smiles,c.inchikey,p.external_id,
        p.name,p.source_id,p.source_url,p.license_label,p.record_hash,p.fetched_at,p.raw_json
        FROM discovery_provenance p JOIN discovery_compounds c ON c.id=p.compound_id
        WHERE p.source_id='chembl-approved' ORDER BY c.id,p.external_id"""
        rows=[]
        for raw in con.execute(query):
            row=dict(raw);row['raw_json_sha256']=hashlib.sha256(row.pop('raw_json').encode()).hexdigest();rows.append(row)
        con.close();assert len(rows)==3417
        write_csv(ref.name,rows)
    drug_rows=read_csv(ref)
    structures={};members=defaultdict(set)
    for row in occurrence:
        cid=int(row['compound_id']);structures[cid]=row['canonical_smiles'];members[row['herb_id']].add(cid)
    for row in drug_rows:
        cid=int(row['compound_id']);smi=row['canonical_smiles']
        assert cid not in structures or structures[cid]==smi
        structures[cid]=smi;members['drug_reference'].add(cid)
    union=set().union(*(members[h] for h in HERBS));drug_ids=sorted(members['drug_reference'])
    assert len(union)==1293 and len(drug_ids)==3417
    assert [len(members[h]) for h in HERBS]==[208,336,103,704]
    fpgen=rdFingerprintGenerator.GetMorganGenerator(radius=2,fpSize=2048,includeChirality=True)
    data={};fps={}
    for cid,smi in sorted(structures.items()):
        mol=Chem.MolFromSmiles(smi);assert mol is not None,cid
        serialized=Chem.MolToSmiles(mol,isomericSmiles=True)
        assert Chem.MolToSmiles(Chem.MolFromSmiles(serialized),isomericSmiles=True)==serialized,cid
        scaffold=MurckoScaffold.MurckoScaffoldSmiles(mol=mol,includeChirality=False)
        data[cid]={'compound_id':cid,'canonical_smiles':smi,'herb_memberships':'|'.join(h for h in HERBS if cid in members[h]),
            'drug_reference_member':cid in members['drug_reference'],'mw':Descriptors.MolWt(mol),'logp':Crippen.MolLogP(mol),
            'tpsa':rdMolDescriptors.CalcTPSA(mol),'hbd':Lipinski.NumHDonors(mol),'hba':Lipinski.NumHAcceptors(mol),
            'rotatable_bonds':Lipinski.NumRotatableBonds(mol),'fraction_csp3':rdMolDescriptors.CalcFractionCSP3(mol),
            'rings':rdMolDescriptors.CalcNumRings(mol),'murcko_scaffold':scaffold,'acyclic':not bool(scaffold),
            'analysis_canonical_smiles':serialized,'serialization_differs_from_source':serialized!=smi}
        fps[cid]=fpgen.GetFingerprint(mol)
    drug_fps=[fps[i] for i in drug_ids];drug_scaffolds={data[i]['murcko_scaffold'] for i in drug_ids}-{''}
    nearest=[]
    for cid in sorted(union):
        sims=np.array(DataStructs.BulkTanimotoSimilarity(fps[cid],drug_fps));j=int(np.argmax(sims))
        scaf=data[cid]['murcko_scaffold']
        data[cid].update({'nearest_drug_id':drug_ids[j],'nearest_drug_tanimoto':float(sims[j]),
                         'exact_drug_structure':cid in members['drug_reference'],'nonempty_scaffold_in_drugs':bool(scaf) and scaf in drug_scaffolds})
        nearest.append({k:data[cid][k] for k in ['compound_id','nearest_drug_id','nearest_drug_tanimoto','exact_drug_structure','nonempty_scaffold_in_drugs']})
    ids=sorted(data);x=np.array([[data[i][k] for k in FEATURES] for i in ids]);assert np.isfinite(x).all()
    scale=StandardScaler().fit(x);pca=PCA(n_components=2,svd_solver='full').fit(scale.transform(x));xy=pca.transform(scale.transform(x))
    for i,v in zip(ids,xy):data[i].update({'pc1':float(v[0]),'pc2':float(v[1])})
    columns=list(data[ids[0]])
    columns=list(dict.fromkeys(k for d in data.values() for k in d))
    write_csv('structure_descriptors.csv',[{k:data[i].get(k,'') for k in columns} for i in ids])
    write_csv('nearest_drug_neighbors.csv',nearest)
    summary=[]
    for group in HERBS+['herb_union','drug_reference']:
        selected=union if group=='herb_union' else members[group];scafs={data[i]['murcko_scaffold'] for i in selected}-{''}
        row={'group':group,'structures':len(selected),'nonempty_scaffolds':len(scafs),'acyclic_structures':sum(data[i]['acyclic'] for i in selected)}
        for feature in FEATURES:
            vals=[data[i][feature] for i in selected]
            for suffix,value in zip(['q25','median','q75'],np.quantile(vals,[.25,.5,.75])):row[feature+'_'+suffix]=float(value)
        if group!='drug_reference':
            vals=[data[i]['nearest_drug_tanimoto'] for i in selected]
            row.update({'nearest_drug_tanimoto_median':float(np.median(vals)),
                'exact_drug_structures':len(selected & members['drug_reference']),
                'nonempty_scaffold_shared_structures':sum(data[i]['nonempty_scaffold_in_drugs'] for i in selected),
                'similarity_at_least_0_5':sum(v>=.5 for v in vals)})
        else:
            row.update({'nearest_drug_tanimoto_median':'','exact_drug_structures':'','nonempty_scaffold_shared_structures':'','similarity_at_least_0_5':''})
        summary.append(row)
    write_csv('group_summary.csv',summary)
    write_csv('pca_loadings.csv',[{'feature':k,'mean':float(scale.mean_[j]),'scale':float(scale.scale_[j]),'pc1':float(pca.components_[0,j]),'pc2':float(pca.components_[1,j])} for j,k in enumerate(FEATURES)])
    # Deterministic exact-match and self-similarity checks catch ID/fingerprint drift.
    assert all(r['nearest_drug_tanimoto']==1. for r in nearest if r['exact_drug_structure'])
    assert all(0<=r['nearest_drug_tanimoto']<=1 for r in nearest)
    assert len(data)==len(union | members['drug_reference'])
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.titlesize':10,'axes.labelsize':8,
        'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'svg.fonttype':'none','savefig.facecolor':'white'})
    fig,axes=plt.subplots(2,2,figsize=(7.3,6.3));fig.subplots_adjust(left=.095,right=.98,bottom=.11,top=.93,wspace=.31,hspace=.47)
    ax=axes[0,0];dxy=np.array([[data[i]['pc1'],data[i]['pc2']] for i in drug_ids])
    ax.scatter(dxy[:,0],dxy[:,1],s=5,color='#BAC4CB',alpha=.28,rasterized=True,label='Drug reference')
    for h,name,col in zip(HERBS,NAMES,COLORS):
        v=np.array([[data[i]['pc1'],data[i]['pc2']] for i in sorted(members[h])]);ax.scatter(v[:,0],v[:,1],s=7,color=col,alpha=.50,rasterized=True,label=name)
    ax.set(xlabel=f'PC1 ({pca.explained_variance_ratio_[0]:.1%}; symlog)',ylabel=f'PC2 ({pca.explained_variance_ratio_[1]:.1%})',title='a  Shared descriptor space')
    ax.set_xscale('symlog',linthresh=3,linscale=1)
    ax.legend(frameon=False,fontsize=6.2,loc='upper right',markerscale=1.5)
    ax=axes[0,1]
    for h,name,col in zip(HERBS,NAMES,COLORS):
        v=np.sort([data[i]['nearest_drug_tanimoto'] for i in members[h]])
        ax.step(v,np.arange(1,len(v)+1)/len(v),where='post',color=col,lw=1.4,label=f'{name} (n={len(v)})')
    ax.set(xlabel='Maximum Tanimoto to drug reference',ylabel='Cumulative fraction',xlim=(0,1.02),ylim=(0,1.03),title='b  Nearest-drug structural similarity')
    ax.legend(frameon=False,fontsize=6.5,loc='lower right')
    ax=axes[1,0];pos=np.arange(4);s=summary[:4]
    exact=[r['exact_drug_structures']/r['structures']*100 for r in s];shared=[r['nonempty_scaffold_shared_structures']/r['structures']*100 for r in s]
    ax.bar(pos-.16,exact,.31,color='#183B56',label='Exact structure');ax.bar(pos+.16,shared,.31,color='#158A88',label='Nonempty Murcko scaffold')
    ax.set(xticks=pos,xticklabels=NAMES,ylabel='Fraction of herb structures (%)',title='c  Different definitions of overlap');ax.tick_params(axis='x',rotation=20)
    ax.legend(frameon=False,fontsize=6.3,loc='upper left')
    ax=axes[1,1];groups=HERBS+['drug_reference'];vals=[[data[i]['mw'] for i in members[g]] for g in groups]
    bx=ax.boxplot(vals,tick_labels=NAMES+['Drug ref.'],patch_artist=True,widths=.55,showfliers=False,medianprops={'color':'white','linewidth':1.2})
    for patch,col in zip(bx['boxes'],COLORS+['#82919C']):patch.set_facecolor(col)
    ax.set(yscale='log',ylabel='Molecular weight (Da; log scale)',title='d  Molecular-size distributions');ax.tick_params(axis='x',rotation=25)
    for ax in axes.flat:
        ax.grid(alpha=.15,axis='y');ax.set_axisbelow(True)
        label=ax.get_title();ax.set_title(r'$\mathbf{'+label[0]+'}$'+label[1:],loc='left');ax.set_title('')
        for spine in ax.spines.values():spine.set_linewidth(1)
    fig.text(.5,.012,'Occurrence and structural similarity describe chemistry; they do not establish efficacy or safety.',ha='center',fontsize=7,color='#4F606B')
    name='figure-07-herbal-chemical-space'
    fig.canvas.draw();renderer=fig.canvas.get_renderer();w,h=fig.canvas.get_width_height()
    for t in fig.findobj(matplotlib.text.Text):
        if t.get_visible() and t.get_text() and not t.get_clip_on():
            b=t.get_window_extent(renderer);assert b.x0>=-1 and b.y0>=-1 and b.x1<=w+1 and b.y1<=h+1,t.get_text()
    figure_files=[]
    for ext in ['svg','pdf','png']:
        p=FIG/f'{name}.{ext}';fig.savefig(p,dpi=600);figure_files.append({'path':str(p.relative_to(ROOT)),'sha256':sha(p),'bytes':p.stat().st_size})
    fig.savefig(FIG/f'{name}.docx.png',dpi=240)
    preview=ROOT/'tmp/manuscript/q1-revision/chemical-space-preview.png';preview.parent.mkdir(parents=True,exist_ok=True);fig.savefig(preview,dpi=160);plt.close(fig)
    caption=('Figure 7. Chemical-space comparison of herb occurrences and a drug reference. '
        '(a) Principal components of eight standardized RDKit descriptors, fitted to the union of unique stored structures across the 1293-structure four-herb retrieval and 3417-structure ChEMBL reference. Each unique structure enters the fit once; membership-specific points can overlap. The PC1 axis uses a symmetric-log display (linear between -3 and 3); no outliers are discarded. '
        '(b) Empirical cumulative distributions of maximum chirality-aware Morgan-fingerprint Tanimoto similarity to all reference structures (radius 2; 2048 bits). Exact matches remain included. '
        '(c) Fractions with exact stored chemical identity or a nonempty, achiral Bemis-Murcko scaffold occurring in the reference. Acyclic empty scaffolds are excluded from scaffold-overlap counts. '
        '(d) Molecular-weight boxes show medians and interquartile ranges; whiskers extend to 1.5 times the interquartile range and points beyond them are omitted from this panel only. The ordinate is logarithmic; all structures enter the summaries. '
        'The ChEMBL group is the archived import labelled chembl-approved, not an independently reverified current regulatory inventory. Neither descriptor proximity, scaffold overlap nor annotation absence is evidence for equivalent activity, safety or chemical novelty. Source: q1_extension/chemistry CSV files.')
    (FIG/f'{name}.caption.txt').write_text(caption+'\n')
    source_paths=[source,ref,Path(__file__),ROOT/'research/manuscript/gqd_retrieval.json']
    result={'passed':True,'analysis':'Post hoc descriptive chemical-space comparison','rdkit_version':rdkit.__version__,
        'herb_structures':len(union),'drug_reference_structures':len(drug_ids),'unique_combined_structures':len(data),
        'changed_smiles_serializations':sum(d['serialization_differs_from_source'] for d in data.values()),
        'distinct_analysis_canonical_smiles':len({d['analysis_canonical_smiles'] for d in data.values()}),
        'fit_features':FEATURES,'pca_explained_variance_ratio':pca.explained_variance_ratio_.tolist(),'groups':summary,
        'sources':[{'path':str(p.relative_to(ROOT)),'sha256':sha(p),'bytes':p.stat().st_size} for p in source_paths],
        'figures':figure_files,'figure_dpi':600,'checks':['Expected cohort denominators','Canonical SMILES round trips','Finite descriptors','Exact IDs imply Tanimoto=1','Similarity range','Unique PCA fit IDs','All figure text inside canvas'],
        'limitations':['Source label chembl-approved is retained, not reverified regulatory status.','Herb groups overlap and are not independent biological cohorts.','PCA is a visualization of standardized descriptors, not a learned biological latent space.','Stored structures preserve source charge/stereochemistry; unknown stereochemistry remains unknown.','No pH, salt, tautomer or stereoisomer enumeration and no activity or safety inference.','Fingerprint similarity 1 can reflect hash collisions; exact identity is checked separately by stored canonical SMILES.']}
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':True,'groups':[{k:r[k] for k in ['group','structures','nonempty_scaffolds','acyclic_structures','mw_median','nearest_drug_tanimoto_median','exact_drug_structures']} for r in summary],'pca':result['pca_explained_variance_ratio']},indent=2))


if __name__=='__main__':main()
