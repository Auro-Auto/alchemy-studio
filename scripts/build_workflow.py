#!/usr/bin/env python3
"""Build the native ComfyUI FLUX.2 Klein 4B two-reference workflows.

The API graph manually expands the official template's two-image subgraph and
its two nested Reference Conditioning subgraphs. It intentionally keeps the
official node IDs, especially LoadImage 76/81 and SaveImage 94.

No models, custom nodes, network access, or inference are required to run this
builder. The unmodified official UI template remains the provenance source.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_ROOT / "workflows" / "official_flux2_klein_4b_distilled.json"
SOURCE_URL = (
    "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/"
    "refs/heads/main/templates/image_flux2_klein_image_edit_4b_distilled.json"
)
SOURCE_SHA256 = "7f1ead62d1456d485e95fb432fb492d2783f5ec9cfeefefe4ef71bfb448ba1a3"
TWO_IMAGE_SUBGRAPH = "65c22b29-59aa-496b-89c6-55a603658670"
MODEL_NAME = "flux-2-klein-4b-fp8.safetensors"
VAE_NAME = "flux2-vae.safetensors"
DEFAULT_TEXT_ENCODER = "qwen_3_4b_fp4_flux2.safetensors"
DEFAULT_PROMPT = "stylize the handbag in image1 with the colours and logo from image 2"
DEFAULT_SEED = 786795143695419
FILENAME_PREFIX = "alchemy/flux2_klein"


def provenance() -> dict[str, Any]:
    return {
        "source_url": SOURCE_URL,
        "source_file": SOURCE_PATH.name,
        "source_sha256": SOURCE_SHA256,
        "source_subgraph": TWO_IMAGE_SUBGRAPH,
        "schema_compatibility": (
            "ComfyUI 0.44.0 /object_info: ImageScaleToTotalPixels uses "
            "resolution_steps for the template's third widget."
        ),
        "adaptation": (
            "Expanded native two-reference graph; reference order image_a then "
            "image_b on both conditioning branches; FP4 text encoder; reduced "
            "pixel budget; fixed seed. The original source file is unchanged."
        ),
    }


def load_official_template() -> dict[str, Any]:
    raw = SOURCE_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(
            "The official source template has changed. Review its graph before "
            f"updating the recorded SHA256. Expected {SOURCE_SHA256}, got {digest}."
        )
    return json.loads(raw)


def node(class_type: str, title: str, **inputs: Any) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def build_api_workflow(
    *,
    prompt: str = DEFAULT_PROMPT,
    seed: int = DEFAULT_SEED,
    megapixels: float = 0.25,
    text_encoder: str = DEFAULT_TEXT_ENCODER,
    image_a: str = "handbag_white.png",
    image_b: str = "comfy_logo_blue.png",
) -> dict[str, Any]:
    """Return a plain /prompt API graph, not a UI workflow or request envelope."""
    graph = {
        "76": node("LoadImage", "Reference A / image1", image=image_a),
        "81": node("LoadImage", "Reference B / image2", image=image_b),
        "107": node(
            "UNETLoader", "FLUX.2 Klein 4B distilled FP8",
            unet_name=MODEL_NAME, weight_dtype="default",
        ),
        "108": node(
            "CLIPLoader", "Qwen3 4B text encoder",
            clip_name=text_encoder, type="flux2", device="default",
        ),
        "110": node("VAELoader", "FLUX.2 VAE", vae_name=VAE_NAME),
        "109": node(
            "CLIPTextEncode", "Two-reference instruction",
            clip=["108", 0], text=prompt,
        ),
        "86": node(
            "ConditioningZeroOut", "Zero text for negative branch",
            conditioning=["109", 0],
        ),
        "111": node(
            "ImageScaleToTotalPixels", "Resize reference A",
            image=["76", 0], upscale_method="nearest-exact",
            megapixels=megapixels, resolution_steps=1,
        ),
        "85": node(
            "ImageScaleToTotalPixels", "Resize reference B",
            image=["81", 0], upscale_method="nearest-exact",
            megapixels=megapixels, resolution_steps=1,
        ),
        "116": node(
            "VAEEncode", "Encode reference A", pixels=["111", 0], vae=["110", 0],
        ),
        "119": node(
            "VAEEncode", "Encode reference B", pixels=["85", 0], vae=["110", 0],
        ),
        # Official nested subgraph 112: append A to positive and negative.
        "117": node(
            "ReferenceLatent", "Positive + reference A",
            conditioning=["109", 0], latent=["116", 0],
        ),
        "115": node(
            "ReferenceLatent", "Negative + reference A",
            conditioning=["86", 0], latent=["116", 0],
        ),
        # Official nested subgraph 84: append B after A on both branches.
        "120": node(
            "ReferenceLatent", "Positive + reference A + B",
            conditioning=["117", 0], latent=["119", 0],
        ),
        "118": node(
            "ReferenceLatent", "Negative + reference A + B",
            conditioning=["115", 0], latent=["119", 0],
        ),
        "114": node("GetImageSize", "Output size from reference A", image=["111", 0]),
        "113": node(
            "EmptyFlux2LatentImage", "Empty output latent",
            width=["114", 0], height=["114", 1], batch_size=1,
        ),
        "102": node(
            "Flux2Scheduler", "Official four-step schedule",
            steps=4, width=["114", 0], height=["114", 1],
        ),
        "101": node("KSamplerSelect", "Euler sampler", sampler_name="euler"),
        "106": node("RandomNoise", "Reproducible noise", noise_seed=seed),
        "103": node(
            "CFGGuider", "Distilled CFG = 1",
            model=["107", 0], positive=["120", 0], negative=["118", 0], cfg=1.0,
        ),
        "104": node(
            "SamplerCustomAdvanced", "Sample two-reference edit",
            noise=["106", 0], guider=["103", 0], sampler=["101", 0],
            sigmas=["102", 0], latent_image=["113", 0],
        ),
        "105": node("VAEDecode", "Decode result", samples=["104", 0], vae=["110", 0]),
        "94": node(
            "SaveImage", "Save alchemy result", images=["105", 0],
            filename_prefix=FILENAME_PREFIX,
        ),
    }
    graph["94"]["_meta"]["provenance"] = provenance()
    return graph


def build_ui_workflow(
    official: dict[str, Any],
    *,
    prompt: str = DEFAULT_PROMPT,
    seed: int = DEFAULT_SEED,
    megapixels: float = 0.25,
    text_encoder: str = DEFAULT_TEXT_ENCODER,
    image_a: str = "handbag_white.png",
    image_b: str = "comfy_logo_blue.png",
) -> dict[str, Any]:
    """Keep the official UI layout/subgraphs, enabling only the two-image path."""
    workflow = copy.deepcopy(official)
    roots = {n["id"]: n for n in workflow["nodes"]}
    roots[76]["widgets_values"][0] = image_a
    roots[81]["widgets_values"][0] = image_b
    for node_id in (75, 9):
        roots[node_id]["mode"] = 4
    for node_id in (92, 94):
        roots[node_id]["mode"] = 0
    roots[94]["widgets_values"] = [FILENAME_PREFIX]
    roots[92]["widgets_values"] = [MODEL_NAME, text_encoder, VAE_NAME, prompt, seed]
    # Even the disabled single-image branch uses the installed encoder.
    roots[75]["widgets_values"][1] = text_encoder

    for subgraph in workflow["definitions"]["subgraphs"]:
        for current in subgraph["nodes"]:
            kind = current["type"]
            if kind == "CLIPLoader":
                current["widgets_values"][0] = text_encoder
            elif kind == "ImageScaleToTotalPixels":
                # ComfyUI 0.44.0: method, megapixels, resolution_steps.
                current["widgets_values"] = ["nearest-exact", megapixels, 1]
            elif kind == "RandomNoise" and current["id"] == 106:
                current["widgets_values"] = [seed, "fixed"]
            elif kind == "CLIPTextEncode" and current["id"] == 109:
                current["widgets_values"] = [prompt]

    workflow.setdefault("extra", {})["alchemy_lab"] = {
        **provenance(), "megapixels": megapixels, "text_encoder": text_encoder,
        "image_a": image_a, "image_b": image_b, "seed": seed,
    }
    roots[97]["widgets_values"] = [
        "# Alchemy Lab: official two-reference workflow\n\n"
        f"- Source: {SOURCE_URL}\n"
        f"- Source SHA256: `{SOURCE_SHA256}`\n"
        f"- Reference order: image1 / node 76, then image2 / node 81.\n"
        f"- Image budget: {megapixels} MP per reference; resolution_steps = 1.\n"
        "- Distilled schedule: 4 steps, Euler, CFG 1.\n"
        "- Single-image branch is bypassed; two-image branch is enabled.\n\n"
        "## Model files\n\n"
        f"- diffusion_models: `{MODEL_NAME}`\n"
        f"- text_encoders: `{text_encoder}`\n"
        f"- vae: `{VAE_NAME}`\n\n"
        "[Official FP8 diffusion model](https://huggingface.co/black-forest-labs/"
        "FLUX.2-klein-4b-fp8)\n\n"
        "[Official Comfy text encoders and VAE](https://huggingface.co/Comfy-Org/"
        "Flux-2-Klein-4B/tree/main/split_files)\n"
    ]
    return workflow


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--megapixels", type=float, default=0.25)
    parser.add_argument("--text-encoder", default=DEFAULT_TEXT_ENCODER)
    parser.add_argument("--image-a", default="handbag_white.png", help="Filename in ComfyUI/input")
    parser.add_argument("--image-b", default="comfy_logo_blue.png", help="Filename in ComfyUI/input")
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "workflows" / "flux2_klein_two_image_api.json",
        help="Plain API graph destination; an adjacent _ui.json is also written",
    )
    args = parser.parse_args()
    if not math.isfinite(args.megapixels) or not 0.01 <= args.megapixels <= 16.0:
        parser.error("--megapixels must be finite and between 0.01 and 16")
    if not 0 <= args.seed <= 0xFFFFFFFFFFFFFFFF:
        parser.error("--seed must be an unsigned 64-bit integer")
    if not args.prompt.strip():
        parser.error("--prompt must not be empty")
    return args


def main() -> None:
    args = parse_args()
    official = load_official_template()
    options = {
        "prompt": args.prompt, "seed": args.seed, "megapixels": args.megapixels,
        "text_encoder": args.text_encoder, "image_a": args.image_a, "image_b": args.image_b,
    }
    output = args.output.resolve()
    ui_stem = output.stem[:-4] if output.stem.endswith("_api") else output.stem
    ui_output = output.with_name(ui_stem + "_ui.json")
    if output == SOURCE_PATH.resolve() or ui_output == SOURCE_PATH.resolve():
        raise ValueError("The original official workflow must not be overwritten")
    write_json(output, build_api_workflow(**options))
    write_json(ui_output, build_ui_workflow(official, **options))
    # JSON stdout is convenient for the runner and safe with non-ASCII paths.
    print(json.dumps({"api": str(output), "ui": str(ui_output), "source_sha256": SOURCE_SHA256}))


if __name__ == "__main__":
    main()
