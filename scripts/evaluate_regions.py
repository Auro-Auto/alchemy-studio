"""Paired, fixed-seed regional-edit ablation. All results, including failures, stay on disk."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import time
import numpy as np
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_workflow import build_api_workflow, node, write_json
from studio.regions import prepare_regions, build_region_graph, finish_region
from studio.prompts import compile_prompt
from studio.vision import prepare_reference

CASES = [
    {"id": "mug", "a": "plain_mug.jpg", "b": "real_cat.png", "recipe": "ceramic", "edits": [[.28,.48,.70,.90]], "protect": [],
     "extra": "Redesign only the front mug body with one shallow sculpted ceramic cat face. Keep the open rim, tea, handle, silhouette and wooden table unchanged."},
    {"id": "tumbler", "a": "iced_drink_cup.png", "b": "comfy_cat.png", "recipe": "ceramic", "edits": [[.20,.39,.78,.91]], "protect": [[0,0,1,.40]],
     "extra": "Make the lower cup body a navy, cream and orange cat-themed ceramic collectible with one sculpted face. Keep the existing transparent lid, straw, position and background unchanged."},
    {"id": "sneaker", "a": "sneaker_black.png", "b": "real_cat.png", "recipe": "sneaker", "edits": [[.47,.35,.60,.60]], "protect": [],
     "extra": "Redesign only the side panel of the black sneaker with a black and white cat face integrated as embroidered fabric and leather relief. Preserve the laces, sole, toe, opening, camera and background."},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default='http://127.0.0.1:8188')
    parser.add_argument('--output', type=Path, default=ROOT/'output/region_evaluation/v1')
    parser.add_argument('--subject-mask', action='store_true')
    args = parser.parse_args()
    destination = args.output
    if (destination/'protocol.json').exists():
        raise SystemExit('Use a new output directory; existing experiment will not be overwritten.')
    destination.mkdir(parents=True, exist_ok=True)
    protocol = {"cases": CASES, "seeds": [42,123], "megapixels": .5, "subject_mask": args.subject_mask,
        "methods": ["global", "latent_mask"], "resolution_steps_A": 16, "sampling_mask": "conservative_source_pixel_coverage_latent_grid",
        "controls": "Identical A, B, prompt, seed, 4-step Euler, CFG1, model and resolution within each pair. Only source latent and edit mask differ.",
        "predefined_checks": {"raw_protection": "Compare MAE outside edit mask against the same VAE-decoded A. Report every pair, including regressions; lower is better for unchanged pixels only.",
            "final_protection": "Exactly zero changed source pixels where edit mask is zero. This verifies restoration, not model quality.",
            "edit_activity": "Report MAE inside mask; a low outside score alone can be a no-op.",
            "human_rubric": "Unfilled 0/1/2 ratings: recognizable character, integrated material, preserved functional parts, clean boundary. 0=failed, 1=partial, 2=clear. Human ratings are required; none inferred from pixel metrics."},
        "limits": "Three selected objects, two seeds; engineering ablation, not a general benchmark or user study. Manual rectangles are additional input."}
    write_json(destination/'protocol.json', protocol)
    rows = []
    sheet_rows = []
    for case in CASES:
        a, b = ROOT/'input'/case['a'], ROOT/'input'/case['b']
        foreground = prepare_reference(a, destination/case['id']/'foreground', 'a', True)['mask_path'] if args.subject_mask else None
        region = prepare_regions(a, destination/case['id']/'mask', case['edits'], case['protect'], subject_mask=foreground)
        for seed in protocol['seeds']:
            outputs = {}
            for method in protocol['methods']:
                directory = destination/case['id']/str(seed)/method
                directory.mkdir(parents=True, exist_ok=True)
                graph = build_api_workflow(prompt=compile_prompt(case['recipe'],'structured',case['extra']),seed=seed,megapixels=.5)
                graph['111']['inputs']['resolution_steps'] = 16
                if method == 'latent_mask': graph = build_region_graph(graph)
                graph['210'] = node('VAEDecode','VAE reconstruction control',samples=['116',0],vae=['110',0])
                graph['211'] = node('SaveImage','VAE control',images=['210',0],filename_prefix='alchemy/eval_vae')
                write_json(directory/'workflow.json',graph)
                command = [sys.executable,str(ROOT/'scripts/run_comfy.py'),'--server',args.server,'--workflow',str(directory/'workflow.json'),'--image-a',f'76={a}','--image-b',f'81={b}','--output-dir',str(directory/'runs'),'--poll-interval','1']
                if method == 'latent_mask': command += ['--upload',f'201={region["latent_mask_path"]}']
                started = time.monotonic()
                result = subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                (directory/'client.log').write_text(result.stdout,encoding='utf-8')
                row = {'case':case['id'],'seed':seed,'method':method,'elapsed_seconds':round(time.monotonic()-started,3),'status':'failed' if result.returncode else 'success'}
                if result.returncode:
                    rows.append(row);write_json(destination/'results.json',rows);continue
                run = json.loads(next((directory/'runs').glob('*/run.json')).read_text())
                raw = next(Path(o['local_path']) for o in run['outputs'] if Path(o['local_path']).name.startswith('node_94_'))
                control = next(Path(o['local_path']) for o in run['outputs'] if Path(o['local_path']).name.startswith('node_211_'))
                image, reference = Image.open(raw).convert('RGB'), Image.open(control).convert('RGB')
                mask = np.asarray(Image.open(region['mask_path']).convert('L').resize(image.size,Image.Resampling.BILINEAR))
                delta = np.abs(np.asarray(image,dtype=np.int16)-np.asarray(reference,dtype=np.int16))
                row.update(raw_outside_mae_vs_vae=float(delta[mask==0].mean()),raw_inside_mae_vs_vae=float(delta[mask>128].mean()),raw_path=str(raw.relative_to(destination)))
                final = finish_region(raw,a,region['mask_path'],directory/'finished',expected_size=region['metadata']['inference_size'])
                row.update(final['metrics']);row['final_path']=str(final['image_path'].relative_to(destination))
                outputs[method]=raw
                if method=='latent_mask': outputs['final']=final['image_path']
                rows.append(row)
                write_json(destination/'results.json',rows)
                print(json.dumps({key:row.get(key) for key in ['case','seed','method','status','raw_outside_mae_vs_vae','raw_inside_mae_vs_vae'] }),flush=True)
            sheet_rows.append((case['id'],seed,[a,b,outputs.get('global'),outputs.get('latent_mask'),outputs.get('final')]))
    width,height=300,245
    sheet=Image.new('RGB',(width*5,height*len(sheet_rows)+32),'#e8e8e8')
    draw=ImageDraw.Draw(sheet)
    for col,label in enumerate(['A / product','B / character','Global raw','Masked raw','Masked + restore']): draw.text((col*width+10,10),label,fill='black')
    for row,(case,seed,paths) in enumerate(sheet_rows):
        for col,path in enumerate(paths):
            if path:
                im=ImageOps.contain(Image.open(path).convert('RGB'),(width-12,height-30))
                sheet.paste(im,(col*width+(width-im.width)//2,32+row*height+20))
            draw.text((col*width+8,32+row*height+3),f'{case} / seed {seed}',fill='black')
    sheet.save(destination/'comparison.jpg',quality=92)
    with (destination/'human_review.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(['case','seed','method','character_0_2','material_0_2','function_0_2','boundary_0_2','reviewer','notes'])
        for row in rows: writer.writerow([row['case'],row['seed'],row['method'],'','','','','',''])


if __name__=='__main__': main()
