# Alchemy Studio integration contract

## Regional editing extension

`POST /api/jobs` additionally accepts `edit_mode: "global" | "region"` (default `global`), `edit_regions` and `protect_regions` (default `[]`). Each list has at most 12 `[x0,y0,x1,y1]` rectangles in normalized EXIF-oriented A coordinates; values must be finite numbers in [0,1], with positive width/height. Booleans are rejected. Region mode requires at least one edit rectangle.

Region mode uses the complete A scene, optionally isolates B, and intersects edit regions with A's foreground when segmentation is available. It overrides the legacy foreground-union background compositor. Manual regions with isolation disabled remain usable without the segmentation model. An empty or too narrow effective region fails before GPU submission. Changing A in the UI clears region geometry; changing B preserves it.

`preprocessing.edit_mask_url` exposes the pixel restoration mask. `preprocessing_provenance.regions` and `region_control` record the actual sampling/restoration masks and inference size. The runner binds `latent_mask_path` to LoadImage node 201; it is distinct from the original-resolution `mask_path` used by final restoration. Candidate metrics include `region_locked`, `raw_protected_mae_255`, `final_protected_mae_255`, `changed_locked_background_pixels`, and metric scope. Raw outputs remain available separately. Final zero change is a pixel restoration guarantee, not a claim about the model's raw output.

Local/SSH application, remote FastAPI at 127.0.0.1:8890, existing ComfyUI at 127.0.0.1:8188. Frontend Vite/React builds into web/dist and FastAPI serves it. Browser uses relative /api URLs. No cloud API or keys. Python package: studio/. Core processing: studio/vision.py. Runtime files: data/.

## HTTP API

- GET /api/status -> {comfy_connected: bool, gpu: string, segmentation_available: bool, active_jobs: number}
- GET /api/presets -> array of {id,title,subtitle,image_a:{id,url},image_b:{id,url},recipe,prompt,preview_url}
- POST /api/uploads multipart field file -> {id,url,width,height}. ID is an opaque project-relative asset path; always use the returned URL for display.
- POST /api/jobs JSON -> {id}. Request: {image_a:string,image_b:string,recipe:string,mode:'structured'|'baseline',prompt:string,isolate_subject:boolean,lock_background:boolean,candidates:1|2|3,seed:integer,megapixels:0.5|1.0}.
- GET /api/jobs -> array of job objects, newest first, maximum 30.
- GET /api/jobs/{id} -> job object described below.
- POST /api/jobs/{id}/cancel -> job object. Cancellation is cooperative between processing/inference stages; do not globally interrupt someone else's ComfyUI jobs.
- GET /api/jobs/{id}/export -> downloadable JSON manifest.
- GET /api/files/{path:path} -> safe local asset response; permits input, output and data only, rejects traversal.

Job: {id,status:'queued'|'running'|'success'|'failed'|'cancelled',stage:string,progress:number (0..100),config:request,created_at:string,elapsed_seconds:number,candidates:[{id:string,url:string,seed:number,score:number|null,metrics:object,download_url:string}],best_index:number,preprocessing:{a_url?:string,b_url?:string,mask_a_url?:string,mask_b_url?:string},error?:string}.

Recipes: ceramic (cat cup), sneaker, plush (collectible plush object), crystal (translucent glass/crystal object), custom. image_a supplies structure; image_b supplies character/appearance. Preset IDs: cat_tumbler, cat_sneaker, real_cat_mug. Structured prompts explicitly set roles, single-object physical integration and recipe material; baseline uses a simple fusion instruction. Freeform prompt is an optional extra instruction.

## Core vision API

Implement in studio/vision.py with Pillow/numpy/onnxruntime. Root will install requirements and acquire model if needed. No automatic unbounded model downloads during an API request.

- segmentation_available() -> bool (model ready locally)
- prepare_reference(source: Path, output_dir: Path, role: str, isolate: bool) -> dict with image_path:Path, mask_path:Path|None, metadata:dict. Preserve canvas/aspect for structure A; appearance B may tight-crop foreground with padding. White/neutral composited image is the model reference, not alpha-only black-background encoding.
- finish_candidate(generated: Path, original_a: Path, output_dir: Path, lock_background: bool, source_mask: Path|None) -> dict with image_path:Path, score:float|None, metrics:dict. Lock background via explicit foreground mask compositing, with original photo geometry/size respected. Explain score as heuristic shape continuity, never a semantic quality guarantee or duplicate-face detector.

Backend must gracefully report unavailable segmentation and reject requested unavailable operations instead of silently pretending they ran. Persist exact actual prompts/workflows/model configuration and all candidates. Do not hide unsuccessful visual samples.
