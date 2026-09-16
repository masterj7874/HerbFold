"""Regenerate manuscript figures from persisted observations, with no inference/API.

SVG/PDF preserve vectors; PNG is 600 dpi. Preview PNGs are only for visual QA.
No original data, coordinates, measurement counts, or model outputs are edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import numpy as np
from Bio.PDB.MMCIF2Dict import MMCIF2Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from verify_af3_msa_comparison import atom_rows, select_ca  # noqa: E402
from herbfold.molecular import _af3_smiles_template  # noqa: E402

NAVY, TEAL, OCHRE, MAGENTA = "#183B56", "#158A88", "#C58B24", "#A54678"
GRAY, LIGHT, INK = "#82919C", "#EAF0F3", "#233746"
COLORS = [NAVY, TEAL, OCHRE, MAGENTA]
CMAP = LinearSegmentedColormap.from_list("evidence", ["#F3F6F8", "#AFD9D4", TEAL, NAVY])
PLDDT = LinearSegmentedColormap.from_list("plddt", [OCHRE, "#E6C573", TEAL, NAVY])
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8.5, "axes.titlesize": 10,
    "axes.labelsize": 8.5, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "legend.fontsize": 7.5, "text.color": INK, "axes.labelcolor": INK,
    "axes.edgecolor": "#BDC9D0", "axes.linewidth": .65,
    "xtick.color": INK, "ytick.color": INK, "grid.color": "#E3E9ED",
    "grid.linewidth": .6, "axes.spines.top": False, "axes.spines.right": False,
    "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
    "savefig.facecolor": "white", "figure.facecolor": "white",
})


class Figures:
    def __init__(self, output):
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.preview = ROOT / "tmp/manuscript-figures"
        self.preview.mkdir(parents=True, exist_ok=True)
        self.sources, self.captions, self.figures, self.data = {}, {}, [], {}
        for code in ("scripts/manuscript_figures.py", "scripts/verify_af3_msa_comparison.py", "src/herbfold/molecular.py"):
            self.track(code)
        self.catalog = self.read("docs/discovery-verification.json")["catalog"]
        self.gqd = self.read("research/manuscript/gqd_retrieval.json")
        self.scale = self.read("docs/scale-validation-results.json")
        self.af3 = self.read("docs/af3-msa-structure-comparison.json")
        self.quantum_receipt = self.read("docs/quantum-projected-verification.json")
        self.quantum = self.quantum_receipt["result"]
        self.quantum_audit = self.read("docs/quantum-projected-independent-audit.json")
        self.bio = self.read("runtime/validation/bio-validation/summary.json")
        self.tox = self.read("runtime/validation/tox21/summary.json")
        assert self.af3["passed"] and self.quantum_receipt["hardware_executed"]
        assert self.scale["attempted"] == sum(self.scale[k] for k in ("retained_unique", "duplicates", "rejected", "known_parent_matches"))
        assert self.quantum_audit["status"] == "passed"
        assert np.array_equal(self.quantum["kernel"], self.quantum_audit["kernel_recomputed"])
        original = self.track(self.quantum_receipt["baseline"]["manifest_path"])
        assert hashlib.sha256(original.read_bytes()).hexdigest() == self.quantum_receipt["baseline"]["manifest_sha256"]
        assert json.loads(original.read_text())["kernel"] == self.quantum_receipt["baseline"]["kernel"]
        self.raw_cache = {}

    def track(self, path):
        path = Path(path)
        if not path.is_absolute(): path = ROOT / path
        key = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        self.sources[key] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        return path

    def read(self, path):
        return json.loads(self.track(path).read_text())

    def raw(self, mode, molecule):
        key = (mode, molecule)
        if key not in self.raw_cache:
            model = self.af3[mode][molecule]["top_ranked_copy"]
            path = self.track(model["structure_path"])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == model["structure_sha256"]
            rows = atom_rows(MMCIF2Dict(str(path)))
            ca, _ = select_ca(rows, "A")
            self.raw_cache[key] = model, rows, ca
        return self.raw_cache[key]

    def save(self, fig, name, caption, data=None):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        width, height = fig.canvas.get_width_height()
        outside = []
        for label in fig.findobj(matplotlib.text.Text):
            if not label.get_visible() or not label.get_text() or label.get_clip_on(): continue
            bounds = label.get_window_extent(renderer)
            if bounds.x0 < -1 or bounds.y0 < -1 or bounds.x1 > width + 1 or bounds.y1 > height + 1:
                outside.append(label.get_text())
        assert not outside, f"Text outside figure {name}: {outside}"
        self.captions[name] = caption
        files = []
        for suffix in ("svg", "pdf", "png"):
            path = self.output / f"{name}.{suffix}"
            fig.savefig(path, dpi=600, bbox_inches=None, metadata={"Creator": "HerbFold reproducible manuscript figures"} if suffix == "pdf" else None)
            files.append({"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
        fig.savefig(self.preview / f"{name}.png", dpi=150)
        (self.output / f"{name}.caption.txt").write_text(caption + "\n")
        self.figures.append({"id": name, "files": files, "size_inches": fig.get_size_inches().tolist(), "png_dpi": 600})
        self.data[name] = data
        plt.close(fig)
        print(name, flush=True)

    def finish(self):
        (self.output / "captions.md").write_text("\n\n".join(f"## {name}\n\n{caption}" for name, caption in self.captions.items()) + "\n")
        report = {"created_at": datetime.now(UTC).isoformat(), "new_inference": False,
                  "new_qpu_jobs": 0, "input_sources": self.sources, "figures": self.figures,
                  "software": {"python": sys.version.split()[0], "matplotlib": matplotlib.__version__, "numpy": np.__version__},
                  "interpretation": "Conceptual diagrams are labeled. Quantitative plots use persisted observations; no fabricated samples or clinical inference."}
        (self.output / "figure-manifest.json").write_text(json.dumps(report, indent=2) + "\n")
        (self.output / "figure-data.json").write_text(json.dumps(self.data, indent=2, allow_nan=False) + "\n")


def title(ax, letter, label):
    ax.set_title(label, loc="left", pad=11, fontweight="medium")
    ax.text(-.10, 1.055, letter, transform=ax.transAxes, fontweight="bold", fontsize=12, va="bottom")


def footer(fig, line):
    fig.text(.07, .018, line, fontsize=7.4, color="#516877", va="bottom")


def box(ax, x, y, w, h, heading, detail, color=NAVY, dashed=False):
    patch = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=.012,rounding_size=.018", edgecolor=color,
                          facecolor="white" if dashed else LIGHT, lw=1, linestyle="--" if dashed else "-")
    ax.add_patch(patch)
    ax.text(x+.025, y+h-.045, heading, fontsize=10, weight="bold", va="top", color=color)
    ax.text(x+.025, y+.028, detail, fontsize=8.3, va="bottom", linespacing=1.6)


def arrow(ax, a, b, color=GRAY, dashed=False):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=12, color=color, lw=1,
                                linestyle="--" if dashed else "-", shrinkA=2, shrinkB=2))


def figure1(f):
    fig, ax = plt.subplots(figsize=(7.4, 5.25)); ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis("off")
    fig.subplots_adjust(left=.035, right=.965, top=.95, bottom=.08)
    ax.text(0, 1.015, "A", fontsize=12, weight="bold")
    box(ax, .02, .72, .27, .25, "Source retrieval", "Botanical taxon assertions\nNatural-product structures\nDrug-reference structures", NAVY)
    box(ax, .37, .72, .27, .25, "Classical generation", "Terminal BRICS proposals\nIdentity + property filters\nDistinct-parent lineage audit", TEAL)
    box(ax, .72, .72, .26, .25, "Selected identities", "SMILES + source / parents\nMeasured counts, not drugs\nExplicit assessment cohorts", NAVY)
    arrow(ax, (.30,.85),(.36,.85)); arrow(ax,(.65,.85),(.71,.85))
    ax.text(0, .635, "B", fontsize=12, weight="bold")
    box(ax, .02, .32, .285, .28, "Quantum descriptors", "4 descriptors; n = 4 molecules\n156 measured qubits\nGlobal / local observables\nIdeal + classical references", TEAL)
    box(ax, .357, .32, .285, .28, "AF3 coordinates", "Selected molecule + PTGS2\nMSA / template provenance\nExact output + confidence\n2 ligands; 5 samples / condition", NAVY)
    box(ax, .695, .32, .285, .28, "Assay evidence", "Assay-specific held-out checks\nCalibration / domain limits\nExact evidence vs prediction\nAbstention retained", MAGENTA)
    for x in (.16,.50,.84): arrow(ax,(.85,.70),(x,.61),color=GRAY)
    ax.text(0, .235, "C", fontsize=12, weight="bold")
    box(ax,.02,.02,.62,.19,"Selection-bound evidence contract", "Molecule + target + job ID + artifact SHA-256\nSeparate studios preserve source and uncertainty", NAVY)
    box(ax,.695,.02,.285,.19,"Research context only", "Gegen Qinlian decoction / T2D\nNo formula-level efficacy test", OCHRE, dashed=True)
    for x in (.16,.50,.84): arrow(ax,(x,.31),(.34,.22),color=GRAY)
    footer(fig, "Conceptual architecture. No quantum-to-affinity shortcut or clinical validation is implied.")
    f.save(fig,"figure-01-evidence-architecture",
      "Figure 1. Conceptual architecture of the implemented evidence workflow. (A) Source assertions and molecular identities remain distinct through classical generation and lineage checks. (B) Quantum descriptor measurements, AlphaFold 3 coordinates and assay evidence are separate computational branches; the diagram does not claim that all generated candidates enter every branch. (C) Selection and artifact identities bind evidence to the viewed molecule and target. Dashed herbal-formula context refers to the motivating literature rather than a formula-level experiment performed in this study. The quantum demonstration contains four molecules; the controlled AF3 comparison contains two ligands and one seed with five diffusion samples per condition. This is a newly drawn conceptual schematic, not experimental pathway evidence.")


def figure2(f):
    fig = plt.figure(figsize=(7.4, 7.9)); gs=fig.add_gridspec(3,2,left=.12,right=.95,bottom=.10,top=.925,hspace=.65,wspace=.68,height_ratios=[.95,1,1])
    ax=fig.add_subplot(gs[0,:]); title(ax,"A","Exact botanical-taxon retrieval in the GQD literature context")
    herbs=["gegen","huangqin","huanglian","gancao"];source_defs=[("coconut-2026-09",NAVY,"COCONUT organism aggregate"),("lotus-2026-04",TEAL,"LOTUS taxon assertions"),("union",OCHRE,"Within-herb union")]
    for j,(source,color,label) in enumerate(source_defs):
        v=[next(r["unique_structures"] for r in f.gqd["counts"] if r["herb_id"]==h and r["source_id"]==source) for h in herbs];x=np.arange(4)+(j-1)*.23;ax.bar(x,v,width=.21,color=color,label=label)
        for xx,n in zip(x,v):ax.text(xx,n+18,str(n),ha="center",fontsize=7)
    ax.set_xticks(range(4),["Gegen","Huangqin","Huanglian","Gancao"]);ax.set_ylabel("Reported structures");ax.set_ylim(0,1080);ax.legend(ncol=1,loc="upper left",frameon=False,fontsize=7)
    herb_union=next(r["union_structures"] for r in f.gqd["union_counts"] if r["source_id"]=="union")
    ax.text(.48,.94,f"Across four herbs: {herb_union:,} unique structures\nSource membership is not material authentication",transform=ax.transAxes,fontsize=7.7,va="top",linespacing=1.5)
    ax=fig.add_subplot(gs[1,0]); title(ax,"B","Imported source coverage")
    sources=f.catalog["sources"]; y=np.arange(3); labels=["COCONUT","LOTUS","ChEMBL"]
    for off,key,color,label in [(-.17,"record_count",NAVY,"Records"),(.17,"compound_count",TEAL,"Unique structures")]:
        vals=[s[key] for s in sources]; ax.barh(y+off,vals,height=.28,color=color,label=label)
        for yy,v in zip(y+off,vals): ax.text(v+12000,yy,f"{v:,}",va="center",fontsize=7)
    ax.set_yticks(y,labels); ax.invert_yaxis(); ax.set_xlim(0,980000); ax.set_xticks([0,400000,800000],["0","0.4","0.8"]); ax.set_xlabel("Count (millions)");ax.legend(loc="lower right",frameon=False,fontsize=7)
    ax=fig.add_subplot(gs[1,1]);title(ax,"C","Five-million-attempt disposition")
    keys=["retained_unique","rejected","duplicates","known_parent_matches"]; vals=[f.scale[k] for k in keys]
    ax.barh(range(4),np.array(vals)/1e6,color=[TEAL,GRAY,OCHRE,MAGENTA],height=.55)
    ax.set_yticks(range(4),["Unique retained","Rejected","Duplicates","Known-parent\nmatches"]);ax.invert_yaxis();ax.set_xlim(0,5.1);ax.set_xlabel("Attempts (millions)")
    for y,v in enumerate(vals):ax.text(v/1e6+.08,y,f"{v:,}",va="center",fontsize=8)
    ax=fig.add_subplot(gs[2,0]);title(ax,"D","Committed generation milestones")
    milestones=f.scale["retained_milestones"]; tx=[0]+[m["generation_seconds"] for m in milestones]+[f.scale["generation_seconds"]]; ry=[0]+[m["committed_retained_unique"]/1e6 for m in milestones]+[f.scale["retained_unique"]/1e6]
    ax.plot(tx,ry,"o-",color=TEAL,lw=1.4,ms=4);ax.set(xlabel="Active generation time (s)",ylabel="Unique retained (millions)",xlim=(0,335),ylim=(0,4.05));ax.grid(axis="y");ax.text(10,3.53,f"{f.scale['retained_per_second']:,.0f} retained / active s",color=TEAL,fontsize=9)
    ax=fig.add_subplot(gs[2,1]);title(ax,"E","Measured execution time")
    prep,gen=f.scale["preparation_seconds"],f.scale["generation_seconds"]
    ax.barh(0,prep,color=OCHRE,height=.45,label="Preparation");ax.barh(0,gen,left=prep,color=TEAL,height=.45,label="Generation")
    ax.text(prep/2,0,f"{prep:.2f}s",ha="center",va="center",fontsize=8);ax.text(prep+gen/2,0,f"{gen:.2f}s",ha="center",va="center",color="white",fontsize=8)
    ax.set(xlim=(0,430),ylim=(-1.5,.65),xlabel="Cumulative active execution time (s)");ax.set_yticks([]);ax.legend(loc="upper center",bbox_to_anchor=(.5,1.02),ncol=2,frameon=False,fontsize=7)
    ax.text(.01,.03,f"16 workers | {f.scale['elapsed_seconds']:.2f}s combined\nSampled summed RSS peak: {f.scale['sampled_peak_aggregate_rss_mb']/1024:.2f} GiB\nFinite pair-space ceiling: {f.scale['compatible_pair_slots_upper_bound']:,}",transform=ax.transAxes,linespacing=1.7,fontsize=7.1)
    footer(fig,"Structures may occur in multiple sources. Timing excludes pauses, post hoc audits, AF3 and QPU execution.")
    f.save(fig,"figure-02-catalog-and-enumeration",
      "Figure 2. Botanical-source retrieval, catalog scale and measured generation. (A) Exact source-taxon membership for the four GQD-context herbs, using Pueraria montana var. lobata/P. lobata, Scutellaria baicalensis, Coptis chinensis and Glycyrrhiza uralensis. COCONUT aggregate organism membership is distinguished from LOTUS taxon-level assertions; neither authenticates a medicinal material or its clinical action. Within-herb unions overlap; the four-herb union is 1,293, not the sum of bars. (B) Provenance records and within-source structures overlap across sources; the complete catalog union is 766,417. (C) Mutually exclusive final outcomes sum to 5,000,000 attempts, with three post-generation lineage exclusions included in rejected proposals. (D) Actual committed checkpoints; connecting lines are guides. (E) Preparation and generation active times exclude pauses, post hoc audits, AF3 and QPU execution. Summed live RSS is sampled and may double-count shared pages. The finite compatible-pair ceiling is not a drug count.", {"herb_counts":f.gqd["counts"],"catalog":f.catalog["sources"],"disposition":dict(zip(keys,vals)),"times_s":tx,"retained_millions":ry})


def sample_points(ax, f, kind, ylabel, limit):
    xpos=[0,.75,2,2.75]; groups=[("baseline","aspirin"),("msa","aspirin"),("baseline","quercetin"),("msa","quercetin")]
    offsets=np.linspace(-.10,.10,5)
    for x,(mode,mol) in zip(xpos,groups):
        entry=f.af3[mode][mol]
        values=[s["summary_metrics"][kind] for s in entry["samples"]] if kind in ("ptm","iptm") else [s[kind]["mean"] for s in entry["samples"]]
        c=OCHRE if mode=="baseline" else TEAL
        ax.scatter(x+offsets,values,s=20,c=c,edgecolors="white",linewidths=.3,zorder=3)
        top=entry["top_ranked_copy"];value=top["summary_metrics"][kind] if kind in ("ptm","iptm") else top[kind]["mean"]
        ax.scatter([x],[value],s=55,marker="D",facecolors="none",edgecolors=NAVY,linewidths=1,zorder=4)
    ax.set_xticks(xpos,["No MSA","MSA+T","No MSA","MSA+T"]);ax.set_xlim(-.38,3.13);ax.set_ylim(*limit);ax.set_ylabel(ylabel);ax.grid(axis="y")
    ax.text(.215,-.24,"Aspirin",transform=ax.transAxes,ha="center",fontsize=8.3);ax.text(.785,-.24,"Quercetin",transform=ax.transAxes,ha="center",fontsize=8.3)


def figure3(f):
    fig=plt.figure(figsize=(7.4,7.35));gs=fig.add_gridspec(3,4,left=.075,right=.965,top=.925,bottom=.12,hspace=.65,wspace=.80,height_ratios=[1,1.30,.95])
    ax=fig.add_subplot(gs[0,:2]);title(ax,"A","pTM: full-complex confidence");sample_points(ax,f,"ptm","pTM",(0,1.05))
    ax=fig.add_subplot(gs[0,2:]);title(ax,"B","ipTM: interface confidence");sample_points(ax,f,"iptm","ipTM",(0,1.05))
    reference=f.track(f.af3["reference"]["path"]); rr=atom_rows(MMCIF2Dict(str(reference)));rca,_=select_ca(rr,"A");rxyz=np.array([a["xyz"] for a in rca.values()]); center=rxyz.mean(0);_,_,view=np.linalg.svd(rxyz-center,full_matrices=False)
    views=[]
    for mode,mol in [("baseline","aspirin"),("msa","aspirin"),("baseline","quercetin"),("msa","quercetin")]:
        model,rows,ca=f.raw(mode,mol);align=model["reference_alignment"];xyz=np.array([ca[k]["xyz"] for k in sorted(ca)]);xyz=xyz@np.array(align["rotation"])+align["translation"];xy=(xyz-center)@view.T
        views.append((mode,mol,model,ca,xy))
    limits=np.max(np.abs(np.concatenate([x[-1][:,:2] for x in views])),axis=0)+7; limit=float(max(limits))
    for i,(mode,mol,model,ca,xy) in enumerate(views):
        ax=fig.add_subplot(gs[1,i]);ax.set_aspect("equal");ax.set(xlim=(-limit,limit),ylim=(-limit,limit));ax.axis("off")
        seg=np.stack([xy[:-1,:2],xy[1:,:2]],axis=1);values=[ca[k]["bfactor"] for k in sorted(ca)][:-1]
        ax.add_collection(LineCollection(seg,cmap=PLDDT,norm=Normalize(0,100),array=np.array(values),linewidths=.65,alpha=.95))
        ax.set_title(f"{mol.title()}\n{'No MSA/templates' if mode=='baseline' else 'MSA + templates'}",fontsize=8.3,pad=8)
        if i==0:ax.text(-.12,1.19,"C",transform=ax.transAxes,fontsize=12,weight="bold")
        ax.plot([-limit+6,-limit+26],[-limit+8]*2,color=INK,lw=1.4);ax.text(-limit+16,-limit+13,"20 Å",ha="center",fontsize=7)
    color_axis=fig.add_axes([.36,.372,.27,.012])
    colorbar=fig.colorbar(matplotlib.cm.ScalarMappable(norm=Normalize(0,100),cmap=PLDDT),cax=color_axis,orientation="horizontal",ticks=[0,50,100])
    colorbar.set_label("Per-Cα pLDDT",fontsize=7,labelpad=1);colorbar.ax.tick_params(labelsize=6.5,pad=1,length=2)
    ax=fig.add_subplot(gs[2,:2]);title(ax,"D","Full-protein Cα confidence");sample_points(ax,f,"full_protein_ca_plddt","Mean Cα pLDDT",(0,105))
    ax=fig.add_subplot(gs[2,2:]);title(ax,"E","Ligand atom confidence");sample_points(ax,f,"ligand_heavy_atom_plddt","Mean ligand pLDDT",(0,105))
    footer(fig,"Dots: five diffusion samples at seed 1. Open diamond: selected top sample, not an additional observation.\nStructures: all 604 Cα atoms, one common rigid reference view and scale; ochre-to-navy = pLDDT 0-100.")
    f.save(fig,"figure-03-alphafold-controlled-comparison",
      "Figure 3. Controlled AF3 comparison for aspirin and quercetin with full-length human PTGS2 (604 residues). (A,B) pTM and ipTM. (C) Original top-ranked C-alpha coordinates, rigidly aligned using all 551 sequence-verified C-alpha correspondences to 5IKR and projected into the same reference-derived orthographic view; all 604 predicted residues remain visible and every panel uses the same scale. No coordinates were relaxed or fabricated. (D,E) Full-protein C-alpha and ligand-heavy-atom mean pLDDT. Each dot is one of five diffusion samples from seed 1; open diamonds mark the selected top sample and are not a sixth sample. No population confidence interval is inferred. Sequence, model-content hash, code, seed, sample count and recycling count were held constant; the comparison changes both MSA and template input. Higher internal confidence and agreement with 5IKR do not establish affinity, clinical efficacy, or independent structural accuracy.")


def matrix(ax, values, label, vmin, vmax):
    im=ax.imshow(values,cmap=CMAP,vmin=vmin,vmax=vmax);labels=["C1","C2","Quercetin","Aspirin"]
    ax.set_xticks(range(4),labels,rotation=25,ha="right");ax.set_yticks(range(4),labels)
    for (i,j),value in np.ndenumerate(values):ax.text(j,i,str(int(value)) if value in (0,1) else f"{value:.4f}",ha="center",va="center",fontsize=7.2,color="white" if value>(vmin+vmax)/2 else INK)
    ax.set_title(label,loc="left",pad=12);return im


def figure4(f):
    fig,axes=plt.subplots(2,2,figsize=(7.4,6.3));fig.subplots_adjust(left=.12,right=.93,top=.9,bottom=.17,hspace=.68,wspace=.60)
    entries=[(f.quantum_receipt["baseline"]["kernel"],"A  Global all-zero return",0,1),
             (f.quantum["kernel"],"B  Measured projected kernel",.93,1),
             (f.quantum["ideal_reference"]["kernel"],"C  Ideal block-circuit reference",.93,1),
             (f.quantum["classical_reference"]["kernel"],"D  Classical descriptor reference",.93,1)]
    for ax,(values,label,lo,hi) in zip(axes.flat,entries):
        im=matrix(ax,np.array(values),label,lo,hi);cb=fig.colorbar(im,ax=ax,fraction=.047,pad=.045);cb.ax.tick_params(labelsize=7);cb.set_label("Kernel / return value",fontsize=7)
    fig.suptitle("One IBM backend, 156 qubits, four molecules",fontsize=12,x=.12,ha="left",y=.98,color=NAVY)
    footer(fig,"A: 10 circuits × 128 shots; raw zeros retained. B: 17 circuits × 1,024 shots; diagonal 1 is defined.\nC1 = brics-8d4157767bb0; C2 = brics-007ed18dee77. A and B-D use explicitly different color scales.")
    f.save(fig,"figure-04-quantum-kernel-comparison",
      "Figure 4. Actual IBM measurements and explicitly separate references for four descriptor vectors on ibm_fez. (A) The original global all-zero return estimate remained zero for every pair, including self pairs; each of ten circuits had 128 shots. (B) Local X/Y/Z observations from 156 qubits in connected blocks yield the projected RBF kernel; 17 circuits had 1,024 shots each. Its diagonal is mathematically defined as one, not measured self-fidelity. (C) Exact ideal simulation of the saved block topology. (D) Classical RBF comparison on the same encoded descriptor inputs. C1 and C2 identify the two recorded generated candidates. Panel A uses scale 0-1 and panels B-D share the restricted 0.93-1 scale to reveal actual differences. This n=4 technical demonstration does not measure binding, establish predictive generalization, or show quantum advantage. Circuit family and shot count changed together.", {"sample_ids":f.quantum["sample_ids"],"kernels":{label:values for values,label,_,_ in entries}})


def figure5(f):
    fig=plt.figure(figsize=(7.4,6.7));gs=fig.add_gridspec(3,2,left=.11,right=.95,top=.92,bottom=.12,hspace=.83,wspace=.56,height_ratios=[1,.8,1])
    q=np.array(f.quantum["plan"]["physical_qubits"]);values=np.array(f.quantum["projected_features"]["values"])
    ax=fig.add_subplot(gs[0,:]);title(ax,"A","Measured local features for quercetin (M3)")
    im=ax.imshow(values[2].T,cmap="RdBu_r",vmin=-1,vmax=1,aspect="auto",extent=(-.5,len(q)-.5,2.5,-.5));ax.set_yticks([0,1,2],["⟨X⟩","⟨Y⟩","⟨Z⟩"]);ax.set_xticks([0,24,48,72,96,120,155]);ax.set_xlabel("Physical qubit index (measurement-register order)");fig.colorbar(im,ax=ax,pad=.015,fraction=.03)
    ax=fig.add_subplot(gs[1,:]);title(ax,"B","State-preparation/readout controls reveal local errors")
    control=f.quantum["controls"]["readout"];zero=np.array(control["zero_error_rates"]);one=np.array(control["one_error_rates"])
    ax.plot(q,100*zero,"o",ms=2.5,color=TEAL,label="Prepared |0>");ax.plot(q,100*one,"^",ms=3,color=MAGENTA,label="Prepared |1>")
    worst=int(np.argmax(one));ax.annotate(f"q{q[worst]}: {100*one[worst]:.2f}%",(q[worst],100*one[worst]),xytext=(q[worst]+16,31),arrowprops={"arrowstyle":"-","color":GRAY},fontsize=8,color=MAGENTA)
    ax.axhline(100*control["mean_error"],ls="--",lw=.8,color=GRAY,label=f"Mean: {100*control['mean_error']:.2f}%")
    ax.set(xlabel="Physical qubit index",ylabel="Error (%)",ylim=(-1,46),xlim=(-2,158));ax.legend(ncol=3,loc="upper left",bbox_to_anchor=(0,.72),fontsize=7,frameon=False)
    ax=fig.add_subplot(gs[2,0]);title(ax,"C","Separate repeat measurement of M1")
    orig=values[0].flatten();duplicate=np.array(f.quantum["controls"]["duplicate"]["features"]).flatten();ax.scatter(orig,duplicate,s=5,color=TEAL,alpha=.5);ax.plot([-1,1],[-1,1],color=GRAY,lw=.8,ls="--");ax.set(xlabel="Original XYZ expectation",ylabel="Repeat XYZ expectation",xlim=(-1.05,1.05),ylim=(-1.05,1.05));ax.text(.04,.95,f"{len(orig)} observables\nK = {f.quantum['controls']['duplicate']['kernel_to_original']:.6f}",transform=ax.transAxes,va="top",fontsize=8)
    ax=fig.add_subplot(gs[2,1]);title(ax,"D","Shot-resampling distributions")
    pairs=[(i,j) for i in range(4) for j in range(i+1,4)];u=f.quantum["kernel_uncertainty"]
    for y,(i,j) in enumerate(pairs):ax.plot([u["lower_95"][i][j],u["upper_95"][i][j]],[y,y],color=TEAL,lw=3);ax.plot(f.quantum["kernel"][i][j],y,"o",ms=4,color=NAVY)
    ax.set_yticks(range(6),[f"M{i+1}-M{j+1}" for i,j in pairs]);ax.invert_yaxis();ax.set_xlabel("Projected kernel");ax.set_xlim(.940,1.001);ax.grid(axis="x")
    footer(fig,"Error includes state preparation. Bars in D: 2.5-97.5% of 64 empirical shot resamples, not a true-kernel CI.\nAdded squared-distance noise can put observed estimates (dots) outside resampling ranges.")
    f.save(fig,"figure-05-observables-and-noise-controls",
      "Figure 5. Raw local observations and noise diagnostics from the projected IBM run. (A) Quercetin X/Y/Z expectations for all 156 measured qubits, shown in recorded physical-qubit order rather than a fabricated chip layout. (B) Per-qubit prepared-state error rates; these include preparation error and are not isolated detector calibrations. Qubit 72 has a prepared-|1> error of 38.5742%, while the mean across both states and all qubits is 1.9165%. (C) The separately executed repeat of the first molecule contains 468 observable estimates; axes plot the two actual sets, not simulated replicates. (D) Six off-diagonal point estimates and uncentered empirical 2.5th-97.5th shot-resampling percentiles from 64 resamples. These ranges exclude systematic drift/readout bias and are not confidence intervals for an ideal or true kernel; finite-shot squared-distance bias can place plug-in estimates outside them. No readout mitigation is applied.")


def figure6(f):
    fig=plt.figure(figsize=(7.4,6.3));gs=fig.add_gridspec(2,2,left=.14,right=.965,bottom=.14,top=.92,hspace=.66,wspace=.62)
    ax=fig.add_subplot(gs[0,0]);title(ax,"A","Assay-model qualification")
    groups=["PTGS2","KCNH2"];n=[sum(m["target"]==g for m in f.bio["models"]) for g in groups];passed=[sum(m["target"]==g and m["quality_status"]=="qualified" for m in f.bio["models"]) for g in groups]
    ax.barh(groups,passed,color=TEAL,label="Qualified under protocol");ax.barh(groups,np.array(n)-passed,left=passed,color=LIGHT,edgecolor=GRAY,label="Not qualified");ax.set_xlabel("Assay-specific models");ax.set_xlim(0,max(n)+.7);ax.legend(loc="lower right",fontsize=7,frameon=False)
    for y,(a,b) in enumerate(zip(passed,n)):ax.text(b+.12,y,f"{a}/{b}",va="center",fontsize=8)
    model=next(m for m in f.bio["models"] if m["quality_status"]=="qualified")
    ax=fig.add_subplot(gs[0,1]);title(ax,"B","hERG binding: held-out error")
    names=["median_baseline","ridge","random_forest"];mae=[model["metrics"][name]["test"]["mae"] for name in names]
    ax.bar(range(3),mae,color=[GRAY,OCHRE,TEAL],width=.6);ax.set_xticks(range(3),["Median","Ridge","RF selected"]);ax.set_ylabel("MAE (pIC50)");ax.set_ylim(0,.76)
    for i,v in enumerate(mae):ax.text(i,v+.025,f"{v:.3f}",ha="center",fontsize=8)
    ax.text(.03,.96,"24 held-out structures",transform=ax.transAxes,va="top",fontsize=8)
    ax=fig.add_subplot(gs[1,0]);title(ax,"C","Applicability to 15,740 candidates")
    total=f.bio["counts"]["candidates"];inside=[f.bio["candidate_applicability"]["KCNH2:IC50"]["in_domain"],f.tox["candidates"]["in_domain"]]
    ax.barh([0,1],np.array(inside)/total*100,color=TEAL,height=.5);ax.barh([0,1],100-np.array(inside)/total*100,left=np.array(inside)/total*100,color=LIGHT,height=.5)
    ax.set_yticks([0,1],["hERG model","Tox21 domain"]);ax.set(xlim=(0,100),xlabel="Candidate fraction (%)");ax.invert_yaxis()
    for y,n in enumerate(inside):ax.text(50,y,f"{n:,} in-domain / {total:,}",ha="center",va="center",fontsize=8)
    ax=fig.add_subplot(gs[1,1]);title(ax,"D","Tox21 held-out ROC AUC")
    endpoints=f.tox["endpoints"]
    for y,e in enumerate(endpoints):
        metric=e["metrics"];lo,hi=metric["roc_auc_ci95"];ax.plot([lo,hi],[y,y],color=GRAY,lw=1);ax.plot(metric["roc_auc"],y,"o",ms=4,color=TEAL)
    ax.set_yticks(range(len(endpoints)),[e["endpoint"] for e in endpoints],fontsize=6.7);ax.invert_yaxis();ax.set(xlabel="ROC AUC",xlim=(.45,1));ax.axvline(.5,ls="--",lw=.7,color=GRAY)
    footer(fig,"Assay-specific computational validation, not efficacy or human safety. Cohort membership is explicit.\nTox21 intervals are scaffold-bootstrap ranges; hERG binding is not a channel-current assay.")
    f.save(fig,"figure-06-assay-performance-and-abstention",
      "Figure 6. Assay validation and candidate applicability are reported together. (A) Qualification under the implemented dataset/split/error criteria by target; no PTGS2 model qualified. (B) Held-out MAE for the single qualified hERG assay CHEMBL1827362, a competitive [3H]dofetilide binding assay. The 150 structures were divided into train 84, tune 15, calibration 27 and test 24; model selection used tuning data. (C) All 15,740 assessed candidates lie outside this hERG model's training-similarity domain, so it supplies zero model-derived candidate predictions. Tox21 applicability is shown separately. Three candidates have exact PTGS2 assay matches; categories are endpoint-specific and can overlap with abstention. The cohort comprises all 5,741 prior-campaign structures plus a uniform 10,000-structure generation sample, with one overlap, not all 3.7 million structures. (D) Actual Tox21 endpoint AUCs with stored scaffold-bootstrap intervals. Tox21 qualification uses these held-out metrics, and all twelve evaluated endpoints are displayed; this gate is not an external validation cohort. These are assay measurements/models, not proof of efficacy or human safety.",{"herg_test_mae":dict(zip(names,mae)),"candidate_total":total,"in_domain":inside})


def supplement1(f):
    fig=plt.figure(figsize=(7.4,6.8));gs=fig.add_gridspec(3,2,left=.11,right=.95,bottom=.12,top=.92,hspace=.8,wspace=.48,height_ratios=[.8,.8,1.05])
    ax=fig.add_subplot(gs[0,:]);title(ax,"A","MSA support varies across the 604-residue target")
    features=f.af3["msa"]["aspirin"]["msa_features"]
    for field,col in [("unpaired",TEAL),("paired",OCHRE)]:
        vals=features[field]["column_counts"];ax.plot(np.arange(1,len(vals)+1),vals,color=col,label=field.title(),lw=1.2)
    ax.set(xlabel="PTGS2 sequence position",ylabel="MSA row count",xlim=(1,604));ax.legend(frameon=False,loc="upper right",ncol=2);ax.grid(axis="y")
    ax=fig.add_subplot(gs[1,0]);title(ax,"B","5IKR agreement: all 551 matched Cα")
    for i,(mode,mol) in enumerate([("baseline","aspirin"),("msa","aspirin"),("baseline","quercetin"),("msa","quercetin")]):
        vals=[s["reference_alignment"]["rmsd_angstrom"] for s in f.af3[mode][mol]["samples"]];ax.scatter(i+np.linspace(-.1,.1,5),vals,color=OCHRE if mode=="baseline" else TEAL,s=18)
    ax.set_xticks(range(4),["A / none","A / MSA","Q / none","Q / MSA"],fontsize=7);ax.set_yscale("log");ax.set_ylabel("Cα RMSD (Å; log scale)");ax.set_ylim(.1,60);ax.grid(axis="y")
    ax=fig.add_subplot(gs[1,1]);title(ax,"C","Templates actually used")
    templates=features["templates"];ax.barh([t["entry_id"] for t in templates],[t["mapped_residues"] for t in templates],color=NAVY,height=.55);ax.set(xlim=(0,655),xlabel="Mapped query residues");ax.invert_yaxis()
    for i,t in enumerate(templates):ax.text(t["mapped_residues"]+8,i,str(t["mapped_residues"]),va="center",fontsize=7.5)
    for i,mol in enumerate(["aspirin","quercetin"]):
        ax=fig.add_subplot(gs[2,i]);title(ax,"D" if i==0 else "E",f"{mol.title()}: actual ligand")
        _,rows,_=f.raw("msa",mol);ligand=[r for r in rows if r["chain"]=="B"];xyz=np.array([r["xyz"] for r in ligand]);center=xyz.mean(0);_,_,view=np.linalg.svd(xyz-center,full_matrices=False);xy=(xyz-center)@view.T
        bonds=_af3_smiles_template(f.af3["msa"][mol]["canonical_smiles"],ligand);assert bonds
        index={r["name"]:j for j,r in enumerate(ligand)}
        for a,b,order,aromatic in bonds:
            if a not in index or b not in index:continue
            a,b=xy[index[a],:2],xy[index[b],:2];delta=b-a;normal=np.array([-delta[1],delta[0]])/np.linalg.norm(delta)
            offsets=[-.065,.065] if order==2 else [0]
            for off in offsets:ax.plot([a[0]+normal[0]*off,b[0]+normal[0]*off],[a[1]+normal[1]*off,b[1]+normal[1]*off],color=GRAY,lw=1.3,ls="--" if aromatic else "-")
        for row,point in zip(ligand,xy):
            color=MAGENTA if row["element"]=="O" else NAVY;ax.scatter(point[0],point[1],s=48,color=color,zorder=3,edgecolors="white",linewidths=.5)
        ax.set_aspect("equal");ax.set_xlabel("Orthographic coordinate (Å)");ax.set_ylabel("Å");ax.grid(alpha=.4)
    footer(fig,"Coverage counts are descriptive, not Neff. Protein agreement is template-sensitive; ligand views are not\nexperimentally validated poses. C: navy; O: magenta. D/E are separate rigid views, not a ligand alignment.")
    f.save(fig,"figure-S1-msa-and-coordinate-audit",
      "Supplementary Figure S1. Raw MSA and coordinate diagnostics. (A) Non-query non-gap row counts at every alignment column from the actual inference input, with lowercase insertions and periods removed. Unpaired and paired alignments contain 11,229 and 17,801 rows including the query; these descriptive counts are not effective evolutionary independence. (B) Every one of the five samples per condition is aligned using all 551 sequence-verified C-alpha correspondences to 5IKR; no trimming or outlier rejection is applied. 5IKR contains mefenamic acid and is not an aspirin/quercetin pose reference. (C) Actual template entries and mapped residue counts; absence of 5IKR as an explicit template does not establish an independent held-out comparison. (D,E) Selected MSA output ligand coordinates in independently centered orthographic views. Bond connectivity is reconstructed from the exact input SMILES with the AF3 atom-name/element mapping verified; atom positions are not regenerated or optimized. These projections show structure provenance, not validated binding poses.")


def supplement2(f):
    fig=plt.figure(figsize=(7.4,6.8));gs=fig.add_gridspec(2,2,left=.14,right=.95,bottom=.13,top=.92,hspace=.62,wspace=.60)
    endpoints=f.tox["endpoints"];ax=fig.add_subplot(gs[0,0]);title(ax,"A","Tox21 Brier score / baseline")
    for y,e in enumerate(endpoints):
        a=e["metrics"]["brier"];b=e["metrics"]["train_prevalence_brier_baseline"];ax.plot([a,b],[y,y],color=GRAY,lw=1);ax.scatter([a,b],[y,y],c=[TEAL,GRAY],s=15)
    ax.set_yticks(range(12),[e["endpoint"] for e in endpoints],fontsize=7);ax.invert_yaxis();ax.set_xlabel("Brier score (lower is better)");ax.grid(axis="x")
    ax=fig.add_subplot(gs[0,1]);title(ax,"B","Uncalibrated reliability: NR-AR")
    path=f.track("runtime/validation/tox21/NR-AR-holdout.jsonl");rows=[json.loads(line) for line in path.read_text().splitlines() if line];scores=np.array([r["score"] for r in rows]);labels=np.array([r["label"] for r in rows]);bins=np.linspace(0,1,11);points=[]
    for left,right in zip(bins[:-1],bins[1:]):
        keep=(scores>=left)&((scores<right) if right<1 else (scores<=right))
        if keep.any():points.append([float(scores[keep].mean()),float(labels[keep].mean()),int(keep.sum())])
    xy=np.array(points);ax.plot([0,1],[0,1],ls="--",color=GRAY,lw=.8);ax.plot(xy[:,0],xy[:,1],color=TEAL,lw=1);ax.scatter(xy[:,0],xy[:,1],s=12+np.sqrt(xy[:,2])*2,color=TEAL,edgecolor="white",linewidth=.5)
    ax.set(xlim=(0,1),ylim=(-.02,1.06),xlabel="Mean uncalibrated score",ylabel="Observed active fraction");ax.text(.40,.08,f"n = {len(rows):,} held-out\nMarker area increases\nwith bin count",transform=ax.transAxes,va="bottom",fontsize=7.4)
    ax=fig.add_subplot(gs[1,0]);title(ax,"C","Endpoint-level candidate predictions")
    ax.barh(range(12),[e["candidate_predictions"] for e in endpoints],color=TEAL,height=.55);ax.set_yticks(range(12),[e["endpoint"] for e in endpoints],fontsize=7);ax.invert_yaxis();ax.set_xlabel("Predicted candidates (of 15,740)");ax.set_xlim(0,3450)
    for y,e in enumerate(endpoints):ax.text(e["candidate_predictions"]+35,y,f"{e['candidate_predictions']:,}",va="center",fontsize=6.7)
    ax=fig.add_subplot(gs[1,1]);title(ax,"D","Cohort-specific applicability")
    t=f.tox["candidates"];prior=t["cohorts"]["prior_campaign_complete"];scale=t["cohorts"]["scale_uniform_sample"]
    ax.barh([0,1],[prior["in_domain"],scale["in_domain"]],color=TEAL,label="In domain");ax.barh([0,1],[prior["out_of_domain"],scale["out_of_domain"]],left=[prior["in_domain"],scale["in_domain"]],color=LIGHT,edgecolor=GRAY,label="Out of domain")
    ax.set_yticks([0,1],["Prior cohort","Uniform scale sample"]);ax.set_xlabel("Candidate structures");ax.set_xlim(0,10500);ax.invert_yaxis();ax.legend(loc="lower right",frameon=False,fontsize=7)
    footer(fig,"Scores refer to in-vitro Tox21 assay activity, not safety probability. NR-AR was chosen as the first listed\nendpoint for the reliability illustration, not by best performance; no post hoc calibration was fitted.")
    f.save(fig,"figure-S2-tox21-calibration-and-coverage",
      "Supplementary Figure S2. Tox21 error, reliability and candidate coverage. (A) Stored held-out Brier scores (teal) and training-prevalence constant-predictor baselines (gray) for all twelve endpoints. (B) A descriptive reliability diagram from actual NR-AR held-out labels and uncalibrated scores; fixed bins of width 0.1 omit empty bins, and marker sizes increase with bin count. NR-AR is the first listed endpoint, not selected for favorable performance. No calibration fit or confidence claim is added. The single deterministic scaffold split places the whole ACYCLIC group in the held-out set (1,671 of 2,738 structures before endpoint-label exclusions), limiting representativeness. (C) Endpoint-specific candidate prediction counts after domain and model requirements; these correlated categories must not be summed as independent candidates. (D) In-domain versus out-of-domain candidates by origin. The cohorts overlap by one structure, and only a uniform 10,000-structure subset of the large generation result was assessed. Assay activity predictions are not safety probabilities or human toxicity determinations.",{"nr_ar_reliability_bins":points,"nr_ar_holdout_n":len(rows)})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path,default=ROOT/"output/manuscript/figures");parser.add_argument("--only",nargs="*")
    args=parser.parse_args();f=Figures(args.output)
    methods=[figure1,figure2,figure3,figure4,figure5,figure6,supplement1,supplement2]
    for method in methods:
        if not args.only or method.__name__ in args.only:method(f)
    f.finish()


if __name__=="__main__":main()
