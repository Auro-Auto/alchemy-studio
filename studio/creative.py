"""Visual treatments and reusable instructions for the two-image workbench."""

from typing import NamedTuple


class Style(NamedTuple):
    name: str
    group: str
    treatment: str


STYLES = {
    "original": Style("原样", "基础", ""),
    "comic": Style("漫画", "纸上", "Use bold ink outlines, flat cel shading, a compact color palette and sparse halftone dots, like a printed comic panel."),
    "ink": Style("水墨", "纸上", "Use East Asian ink-wash painting on warm absorbent paper, with dry-brush contours, layered ink blooms and deliberate areas of bare paper."),
    "watercolor": Style("绘本水彩", "纸上", "Paint a children's picture-book watercolor with translucent pigment washes, granulated color pooling, feathered edges and visible cold-pressed white paper."),
    "pencil": Style("彩铅涂鸦", "纸上", "Draw on a cream sketchbook page with waxy colored-pencil strokes, coarse crosshatching, playful uneven contours and visible gaps between strokes."),
    "papercut": Style("套色剪纸", "纸上", "Make a bold red-and-cream flat papercut with crisp scissor-cut silhouettes, delicate pierced openings and a few layered vermilion paper shapes."),
    "woodcut": Style("木刻版画", "纸上", "Make a black ink woodcut print on unbleached paper, with carved white grooves, broken grain lines and dense areas of rough black ink."),
    "collage": Style("撕纸拼贴", "纸上", "Assemble a handmade magazine collage from torn sheets of mustard, teal and coral paper. Show ragged white paper fibers, mismatched halftone textures, overlapping edges and glue shadows."),
    "clay": Style("黏土", "立体", "Render as a handcrafted stop-motion clay miniature with matte polymer clay, softly rounded volumes, tiny tool marks and soft studio shadows."),
    "felt": Style("羊毛毡", "立体", "Sculpt the recognizable form entirely from dense needle-felted wool. Show fuzz, colored tufts and felt seams in a cozy close-up craft photograph."),
    "vinyl": Style("搪胶公仔", "立体", "Turn the recognizable form into a stylized designer vinyl collectible with oversized rounded features, molded joins, smooth glossy highlights and a pastel display plinth."),
    "bricks": Style("积木模型", "立体", "Build the subject out of small interlocking plastic bricks. Show clear rows of circular studs, block seams, stepped contours and crisp reflections on primary-colored plastic."),
    "popup": Style("立体纸雕", "立体", "Build a small pop-up scene rising from an open folded cardstock spread. Distinguish stacked cut edges, visible hinges, folded paper tabs and soft contact shadows between layers."),
    "film": Style("胶片抓拍", "镜头", "Photograph as an unposed daylight 35 mm film snapshot with gentle analog grain, warm sunlit highlights, muted colors and soft shadow falloff."),
    "flash": Style("直闪快照", "镜头", "Photograph with a compact digital camera and harsh direct flash. Use a near-black background, hard cast shadows, hot specular highlights and a slightly tilted candid frame."),
    "cinema": Style("电影夜景", "镜头", "Stage a cinematic night close-up with a cool blue rim light, one warm practical light, deep shadow, a touch of haze and restrained amber bokeh."),
    "product": Style("极简棚拍", "镜头", "Photograph as a clean studio packshot on a seamless pale backdrop, with a large softbox reflection, a grounded contact shadow and carefully controlled highlights."),
    "macro": Style("微缩摄影", "镜头", "Photograph as a handmade tabletop diorama with tiny scale cues, a close macro lens, a narrow plane of focus and rapid tilt-shift blur in foreground and background."),
    "pixel": Style("像素", "图形", "Use low-resolution 16-bit pixel art with large sharp square pixel clusters, a limited palette, stepped silhouettes and block dithering."),
    "poster": Style("复古海报", "图形", "Render a vintage two- or three-color screenprint, with broad ink shapes, slight registration offsets and subtle paper grain."),
    "sticker": Style("表情贴纸", "图形", "Draw a single die-cut expression sticker with clean vector shapes, exaggerated facial features, a thick white contour and a plain pastel field around it."),
    "blueprint": Style("工程蓝图", "图形", "Depict the subject in precise white technical linework on deep cyan blueprint paper, with a fine drafting grid and sparse construction lines, keeping the object silhouette prominent."),
}


class Instruction(NamedTuple):
    id: str
    name: str
    group: str
    prompt: str
    recipe: str | None = None


INSTRUCTIONS = (
    Instruction("keep_shape", "保形换色", "二图融合",
                "保留 A 的轮廓、比例和功能部件。提取 B 的主色和少量点缀色，分配到 A 原有的不同表面。"),
    Instruction("material_swap", "只换材质", "二图融合",
                "把 B 中最明显的材质纹理用到 A 表面。保留 A 的结构、构图与光照方向，让高光和阴影符合新材质。", "custom"),
    Instruction("morph", "把两样东西长在一起", "二图融合",
                "用 A 做外形骨架，把 B 最有辨识度的一个部位融进 A 正面。交界沿原物曲面连续过渡，做成一个完整的东西。", "custom"),
    Instruction("packshot", "给成品拍产品照", "二图融合",
                "把融合后的主体放在干净的单色背景上，完整展示轮廓，用柔和侧光凸显 B 带来的材质与细节。"),
    Instruction("toy", "把人物做成盲盒", "人物宠物",
                "用 A 作为人物身份与服装参考，保留发型、脸部特征和招牌姿势，参考 B 的配色做成一只圆润的收藏公仔，旁边留一点展示空间。", "custom"),
    Instruction("pet_figurine", "宠物纪念小像", "人物宠物",
                "沿用 A 的底座与视角，把 B 中的宠物做成一只小像。保留耳形、鼻头颜色和最醒目的两处花纹。", "custom"),
    Instruction("outfit", "保留脸和姿势，只换装", "人物宠物",
                "A 的人脸、发型、姿势与镜头角度保持一致。只把 B 的服装轮廓、配色和面料换到 A 身上，衣褶随原姿势自然弯曲。", "custom"),
    Instruction("pet_mascot", "宠物上身变吉祥物", "人物宠物",
                "让 A 像被 B 的宠物附身一样活过来。保留 A 的辨识部件，把 B 的耳朵、眼神和招牌花纹缩成三个容易认出的特点。", "custom"),
    Instruction("weather_pet", "口袋天气兽", "人物宠物",
                "把 A 中的宠物塑成一团有体积的云，保留耳朵、鼻尖和尾巴的轮廓。借 B 的颜色做云层阴影，在胸口露出一条细小闪电。", "custom"),
    Instruction("reaction", "让物品露出同款表情", "热梗",
                "A 仍是主角，把 B 最有辨识度的眼神、嘴形或手势移到 A 身上。表情放大一点，背景简洁，让人一眼看懂。", "meme"),
    Instruction("deadline", "DDL 一切都好", "热梗",
                "让 A 中的主角淡定地保持 B 的表情或姿势。周围作业和通知堆成小山，主角却像什么都没发生一样。用画面讲笑点。", "meme"),
    Instruction("meme_cast", "热梗换主角", "热梗",
                "让 A 的人物或宠物接替 B 梗图的主角。保留 A 的脸和特征，借用 B 的视角、姿势、道具与场景。保留原梗最容易认出的动作。", "meme"),
    Instruction("campus_meme", "梗图闯进校园", "热梗",
                "让 B 中的表情或角色出现在 A 的校园场景里。把夸张动作放在前景，保留背景地标的轮廓，做成不用配字也能看懂的反应图。", "meme"),
    Instruction("gate_relief", "望江北门上杯身", "川大文创",
                "以 A 的杯形为底，把 B 的望江北门做成正面浅浮雕。保留中间高、两边低的三段屋顶和朱红方柱，杯口、把手或杯盖保持原样。", "architecture"),
    Instruction("campus_badge", "校园地标做徽章", "川大文创",
                "以 A 的徽章轮廓为底，将 B 的校园地标简化为三到五块清楚的珐琅色面。保留地标最易识别的屋顶和柱形，控制留白。", "custom"),
    Instruction("campus_postcard", "校园明信片", "川大文创",
                "保留 A 中校园建筑的透视与屋顶、柱廊轮廓，用 B 的色彩和光线整理画面。给天空或边缘留出可手写的干净空位。", "custom"),
    Instruction("local_edit", "只改选中区域", "细节处理",
                "只在选中的区域融合 B 的细节，边缘沿 A 原有的轮廓和光线过渡。保留 A 的其余内容、取景和背景。"),
    Instruction("clean_sign", "清掉多余文字", "细节处理",
                "清掉不需要的文字、标记和伪字，用邻近的底色与纹理自然补齐。保留牌匾或物品的原有形状、明暗和边缘。"),
    Instruction("keep_parts", "保留功能部件", "细节处理",
                "保留 A 现有的开口、连接处和可用部件，沿用它们原有的位置与数量。把 B 的纹理或装饰放在其余表面。"),
)
