from pathlib import Path
import json,subprocess,hashlib,concurrent.futures,time
root=Path(__file__).resolve().parents[1]
models=json.loads((root/'models.json').read_text())
def download(m):
 dest=root/'backend'/'ComfyUI'/'models'/m['folder']/m['name']
 dest.parent.mkdir(parents=True,exist_ok=True)
 part=dest.with_suffix(dest.suffix+'.part')
 if not dest.exists():
  print('START',m['name'],flush=True)
  subprocess.run(['curl','-fL','--retry','5','--retry-delay','3','--connect-timeout','25','--continue-at','-','--silent','--show-error','--output',str(part),m['url']],check=True)
  if part.stat().st_size!=m['size']:raise RuntimeError('Wrong size '+m['name']+' '+str(part.stat().st_size))
  candidate=part
 else:candidate=dest
 h=hashlib.sha256()
 with candidate.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 if h.hexdigest()!=m['sha256']:raise RuntimeError('SHA256 mismatch '+m['name'])
 if candidate==part:part.rename(dest)
 print('VERIFIED',m['name'],m['size'],h.hexdigest(),flush=True)
 return m['name']
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 for name in pool.map(download,models):print('READY',name,flush=True)
print('ALL_MODELS_VERIFIED',flush=True)
