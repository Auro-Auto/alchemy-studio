"""Build the isolated two-reference FLUX 3 Image Klein INT8 benchmark graph."""

from __future__ import annotations

import math
import re
from typing import Any

from scripts.build_workflow import VAE_NAME, build_api_workflow, node
from studio.regions import build_region_graph


FLUX3_MODEL_NAME = "flux3-image-klein-int8-convrot.safetensors"
FLUX3_TEXT_ENCODER = "qwen3vl_4b_int8_convrot.safetensors"
OFFICIAL_TEMPLATE_URL = (
    "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/"
    "templates/image_flux3_klein_distilled_image_edit.json"
)
OFFICIAL_TEMPLATE_SHA256 = "75037b9482769ab98f9d00ac304899c48b9b46b9238686eca19ede42c40924cc"
COMFY_COMMIT = "bdb0816a292d221efeb6ec4ae41bcdff3f91fc86"
_REFERENCE_ALIAS = re.compile(r"\bimage\s*([12])\b", re.IGNORECASE)


def build_flux3_benchmark_graph(
    prompt: str,
    seed: int,
    megapixels: float = .5,
    masked: bool = True,
    *,
    image_a: str = "reference_a.png",
    image_b: str = "reference_b.png",
    text_encoder: str = FLUX3_TEXT_ENCODER,
    model_name: str = FLUX3_MODEL_NAME,
    reference_aliases: bool = True,
) -> dict[str, Any]:
    """Use official FLUX 3 sampling with the project's input and mask geometry.

    Upload A/B at nodes 76/81. For a masked run also upload the project's
    edit_mask_latent.png at node 201. The generated PNG is saved at node 94.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be nonempty")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 0xffffffffffffffff:
        raise ValueError("seed must be an unsigned 64-bit integer")
    if (
        isinstance(megapixels, bool)
        or not isinstance(megapixels, (int, float))
        or not math.isfinite(megapixels)
        or not .01 <= megapixels <= 16
    ):
        raise ValueError("megapixels must be finite and between 0.01 and 16")

    text = (
        _REFERENCE_ALIAS.sub(lambda match: f"<ref_image_{int(match[1]) - 1}>", prompt)
        if reference_aliases else prompt
    )
    # Keep the same A/B preprocessing, RGB mask path and 16x VAE geometry
    # as FLUX.2. Only the nodes required by the new workflow are retained.
    common = build_api_workflow(
        prompt=text, seed=seed, megapixels=megapixels, image_a=image_a, image_b=image_b,
    )
    shared_ids = ["76", "81", "109", "86", "111", "85", "105"]
    if masked:
        common = build_region_graph(common)
        shared_ids.extend(("114", "116", "201", "202", "203", "204"))
    graph = {key: common[key] for key in shared_ids}
    graph["107"] = node(
        "UNETLoader", "FLUX 3 Image Klein distilled INT8",
        unet_name=model_name, weight_dtype="default",
    )
    graph["108"] = node(
        "CLIPLoader", "Qwen3-VL-4B INT8 for FLUX 3",
        clip_name=text_encoder, type="flux3", device="default",
    )
    graph["110"] = node("VAELoader", "Shared FLUX.2 VAE", vae_name=VAE_NAME)
    graph["120"] = node(
        "ReferenceMultiLatent", "Condition on structure A, then appearance B",
        positive=["109", 0], negative=["86", 0], vae=["110", 0],
        resolution="keep resolution",
        **{"images.image0": ["111", 0], "images.image1": ["85", 0]},
    )
    graph["104"] = node(
        "KSampler", "Official FLUX 3 distilled schedule",
        model=["107", 0], seed=seed, steps=16, cfg=1.0, sampler_name="euler",
        scheduler="simple", denoise=1.0, positive=["120", 0], negative=["120", 1],
        latent_image=["204", 0] if masked else ["120", 2],
    )
    graph["94"] = node(
        "SaveImage", "Save FLUX 3 benchmark result", images=["105", 0],
        filename_prefix="alchemy/benchmark_flux3_klein_int8",
    )
    graph["94"]["_meta"]["provenance"] = {
        "official_template_url": OFFICIAL_TEMPLATE_URL,
        "official_template_sha256": OFFICIAL_TEMPLATE_SHA256,
        "comfyui_commit": COMFY_COMMIT,
        "prompt_upsampler": False,
        "prompt_adapter": (
            "Image 1 → <ref_image_0>; Image 2 → <ref_image_1> (case insensitive)"
            if reference_aliases else "verbatim"
        ),
        "reference_order": ["structure_A", "appearance_B"],
        "same_preprocessing_as_flux2": True,
        "masked": masked,
        "masked_source_encoded_separately": masked,
    }
    return graph
