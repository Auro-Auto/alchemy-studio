"""Deterministic, inspectable two-reference instruction compiler."""

from .creative import STYLES

RECIPES = {"ceramic", "sneaker", "plush", "crystal", "architecture", "meme", "custom"}
ROLE_RULES = (
    "Create exactly one coherent physical object from two references. "
    "Image 1 is the structure reference: preserve its main silhouette, camera angle, "
    "proportions and recognizable functional parts. Image 2 supplies the character, "
    "distinctive colors, markings and selected appearance details. Integrate those "
    "details into the object's material and geometry, not a separate creature beside it "
    "and not a pasted rectangular photograph. If a face is used, put exactly one face "
    "on the front, with one pair of eyes, one nose and one mouth; do not repeat faces "
    "around the sides or back. Keep the design readable and physically integrated. "
)
RECIPE_RULES = {
    "ceramic": (
        "Use realistic smooth glazed ceramic and subtle sculpted shallow relief. "
        "Preserve only the functional parts actually present in image 1: for an open mug "
        "keep its open drinking rim and existing handle; for a lidded tumbler retain its "
        "existing lid and straw. Do not invent a straw or lid for an open mug. "
        "Use the character markings as glaze regions, with plain uncluttered sides."
    ),
    "sneaker": (
        "The result is one wearable premium sneaker. Retain the sole, toe, lacing, heel "
        "and opening from image 1. Translate image 2 into leather/fabric panels, restrained "
        "sculpted details and trim. No separate animal sitting on or inside the shoe."
    ),
    "plush": (
        "Transform the structure into one collectible plush toy with soft fabric, "
        "visible seams, embroidered details and gentle rounded volume. Keep the main "
        "object silhouette and identifiable structural parts from image 1 while using "
        "the color pattern and character details of image 2."
    ),
    "crystal": (
        "Create one translucent sculpted glass or crystal collectible with believable "
        "refraction, polished edges and restrained color inclusions from image 2. "
        "Preserve the structure from image 1. Maintain clear readable boundaries; "
        "the entire object must not dissolve into transparent background noise."
    ),
    "custom": (
        "Respect the custom transformation below while keeping the two reference roles "
        "and a single integrated object. Render consistent material, lighting and scale."
    ),
}


def compile_prompt(
    recipe: str, mode: str, extra: str = "", lock_background: bool = False, *, style: str = "original",
) -> str:
    if recipe not in RECIPES or mode not in {"baseline", "structured"} or style not in STYLES:
        raise ValueError("Unknown recipe, generation mode or style")
    if mode == "baseline":
        if recipe == "meme":
            prompt = "Combine images 1 and 2 into a funny reaction picture."
        else:
            prompt = "Combine the object in image 1 and the character or appearance in image 2 into one creative object."
    elif recipe == "meme":
        prompt = (
            "Create one funny reaction picture with a readable focal point. "
            "Image 1 supplies the recognizable object or scene. Image 2 supplies the reaction, "
            "facial expression or visual gag. Integrate the two into a single moment with a "
            "clear, exaggerated contrast. Tell the joke visually with an image-only composition."
        )
    elif recipe == "architecture":
        prompt = (
            "Image 1 defines the existing product, perspective, lighting and functional parts. "
            "Image 2 supplies only the building's silhouette, roof tiers, columns and colors. "
            "Integrate one shallow glazed ceramic relief into the visible front of the product. "
            "Keep the open spaces between columns as plain white glaze, and use a blank plaque. "
            "Leave the rest of the product and scene as in image 1."
        )
    else:
        prompt = ROLE_RULES + RECIPE_RULES[recipe]
    if STYLES[style].treatment:
        prompt += " Apply this look to the edited content. " + STYLES[style].treatment
    if lock_background:
        prompt += " Preserve the object position and framing of image 1; keep the surrounding scene unchanged."
    if extra.strip():
        prompt += (
            "\n\nUser's additional design instructions (including Chinese; prioritize these "
            "for the requested design and actual functional parts):\n" + extra.strip()
        )
    return prompt
