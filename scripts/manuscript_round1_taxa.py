"""Attach the inspected POWO crosswalk without replacing occurrence assertions."""
from pathlib import Path
import argparse,csv,json,hashlib
ROOT=Path(__file__).resolve().parents[1]

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--output',type=Path,default=Path('tmp/round1-replay/taxa'))
    out=a.parse_args().output.resolve()
    if not any(out.is_relative_to(ROOT/x) for x in ['tmp','reproduced']):raise ValueError('Output must be under tmp/ or reproduced/')
    out.mkdir(parents=True,exist_ok=True)
    src=ROOT/'research/manuscript';source=src/'tables/gqd_provenance_records.csv'
    with source.open() as f: rows=list(csv.DictReader(f))
    taxa=json.loads((src/'round1/related_work/powo_taxon_crosswalk.json').read_text())['taxa']
    lookup={x['name_without_authority']:x for x in taxa};enriched=[]
    for row in rows:
        matches=[lookup[t] for t in row['matched_taxon_tokens'].split('|')]
        enriched.append({**row,'powo_record_ids':'|'.join(x['powo_id'] for x in matches),'powo_record_urls':'|'.join(x['url'] for x in matches),'powo_accepted_name_ids':'|'.join(x['accepted_name_id'] for x in matches),'powo_verified_on':'2026-09-09','taxon_crosswalk_scope':'Nomenclature only; original query and source fields preserved'})
    p=out/'botanical_occurrences_with_powo.csv'
    with p.open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(enriched[0]));w.writeheader();w.writerows(enriched)
    print(json.dumps({'rows':len(enriched),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'original_columns_preserved':all(all(a[k]==b[k] for k in a) for a,b in zip(rows,enriched))}))
if __name__=='__main__':main()
