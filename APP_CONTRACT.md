# 接口文档

Studio 运行于 `127.0.0.1:8890`，通过 ComfyUI `127.0.0.1:8188` 推理。FastAPI 提供 `web/dist/` 页面，浏览器使用相对 `/api` 地址。Python 包位于 `studio/`，任务与上传文件位于 `data/`。一个 Studio worker 内的生图和提示词增强共用 GPU 调度锁。

## HTTP 接口

| 方法与路径 | 请求 | 响应 |
| --- | --- | --- |
| `GET /api/status` | — | `comfy_connected`、`gpu`、`segmentation_available`、`active_jobs` |
| `GET /api/presets` | — | 示例数组，含 `id`、`title`、`subtitle`、A/B 素材、用途、指令和预览地址 |
| `GET /api/creative-options` | — | `styles` 与 `instructions` 数组 |
| `POST /api/uploads` | multipart 字段 `file` | `id`、`url`、`width`、`height`、`compressed` |
| `POST /api/jobs` | 生成参数 | HTTP 202，`{id}` |
| `GET /api/jobs` | 可选 `compact=true` | 按创建时间倒序排列的最近 30 个任务 |
| `GET /api/jobs/{id}` | — | 完整任务对象 |
| `POST /api/jobs/{id}/cancel` | — | 更新后的任务对象 |
| `GET /api/jobs/{id}/export` | — | 可下载的任务 JSON |
| `POST /api/prompts/enhance` | 增强参数 | `enhanced_prompt`、`model` |
| `POST /api/prompts/enhance/stream` | 增强参数 | SSE 文本流 |
| `GET /api/files/{path}` | 可选 `download=true` | 素材或结果文件 |

素材 ID 为项目相对路径，页面使用接口返回的 `url`。文件接口只允许 `input/`、`output/`、`data/`，拒绝越界路径。

上传支持静态 JPEG、PNG、WebP，文件不超过 20MB。图片按 EXIF 校正方向，转为 RGB，透明部分合成白底；可处理的高像素图等比缩至 20MP 内。响应尺寸对应规范化后文件，缩放前尺寸写入本地元数据的 `compressed_from`。

## 生成参数

| 字段 | 类型与取值 | 默认值 |
| --- | --- | --- |
| `image_a`、`image_b` | 素材 ID，必填 | — |
| `recipe` | `ceramic`、`sneaker`、`plush`、`crystal`、`architecture`、`meme`、`custom` | `ceramic` |
| `style` | `/api/creative-options` 中的风格 ID | `original` |
| `mode` | `structured`、`baseline` | `structured` |
| `prompt` | 最多 6000 字符 | 空字符串 |
| `isolate_subject` | 布尔值 | `false` |
| `lock_background` | 布尔值 | `false` |
| `edit_mode` | `global`、`region` | `global` |
| `region_detail` | 布尔值 | `false` |
| `edit_regions`、`protect_regions` | 每类最多 12 个矩形 | `[]` |
| `candidates` | 整数 1–3 | `1` |
| `seed` | 0–2⁶⁴−1 的整数 | `42` |
| `megapixels` | `0.5`、`1.0` | `0.5` |

不接受额外字段。整数与数值参数不接受布尔值。A 通常提供结构或场景，B 提供外观；具体角色由用途和用户指令确定。`structured` 编译参考图职责、用途和材质要求，`baseline` 使用简短融合指令。

## 任务结果

任务状态为 `queued`、`running`、`success`、`failed`、`cancelled`。主要字段包括：

- `id`、`created_at`、`elapsed_seconds`：任务标识与时间。
- `status`、`stage`、`progress`、`error`：运行状态；进度表示已完成的处理阶段，不是扩散采样百分比。
- `config`：生成参数。
- `candidates`、`best_index`：候选及推荐索引；每个候选含 `id`、`url`、`download_url`、`seed`、`score` 与 `metrics`。
- `preprocessing`：参考图、分割和区域蒙版地址。
- `models`、`workflow_provenance`、`preprocessing_provenance`、`region_control`：模型、工作流和区域处理记录。

`score` 根据轮廓、配色和前景完整度估算，不衡量语义、身份、审美或重复脸。取消在处理阶段之间生效，不会全局中断其他 ComfyUI 任务。分割不可用时，要求提取主体或整图保留背景的请求返回错误。

## 区域编辑

矩形格式为归一化的 `[x0, y0, x1, y1]`，基于方向校正后的 A。坐标须为 `[0,1]` 内的有限数值，宽高为正，不接受布尔值。`edit_mode=region` 至少需要一个修改区，`region_detail=true` 仅用于区域编辑。

区域模式保留 A 的完整画布，可按需提取 B 的主体。分割可用时，修改区与 A 的主体前景相交；关闭主体提取后，未安装分割模型也可使用手动区域。有效区域为空或过窄时，在提交 GPU 任务前返回错误。更换 A 会清空选区，更换 B 保留选区。

`preprocessing.edit_mask_url` 为像素恢复蒙版。`preprocessing_provenance.regions` 与 `region_control` 包含采样蒙版、恢复蒙版及推理尺寸。`latent_mask_path` 绑定工作流 LoadImage 节点 201；最终恢复使用源分辨率的 `mask_path`。区域模式替代整图前景并集合成策略。

区域指标包括 `region_locked`、`raw_protected_mae_255`、`final_protected_mae_255`、`changed_locked_background_pixels` 与指标范围。原始生成和最终合成分别保存；最终保护区零变化来自像素恢复，不表示模型原始输出未改变。

## 提示词增强

请求字段为 `prompt`、`recipe`、`style`、`edit_mode`。`prompt` 为必填的非空文字，最多 6000 字符；其他字段默认 `custom`、`original`、`global`。增强器根据文字整理要求，不读取参考图片。

流式接口以 `data: JSON` 发送 `stage`、`delta`、`stalled`、`done` 或 `error` 事件。`done` 含完整 `enhanced_prompt` 与模型标识。任务繁忙时拒绝新的增强请求；增强期间提交的生图进入队列。取消或断开增强流后，子进程回收完成才释放 GPU 调度锁。

## 图像处理

实现位于 `studio/vision.py`，使用 Pillow、NumPy 与 ONNX Runtime。模型在安装阶段下载，API 请求不自动下载权重。

| 函数 | 返回值与用途 |
| --- | --- |
| `segmentation_available()` | `bool`，检查分割模型及本地运行环境 |
| `prepare_reference(source, output_dir, role, isolate)` | `image_path`、`mask_path`、`metadata`；A 保留画布和宽高比，B 可按前景裁剪并留边，参考图合成到中性背景 |
| `finish_candidate(generated, original_a, output_dir, lock_background, source_mask, ...)` | `image_path`、`score`、`metrics` 等；保存原始输出，按蒙版合成并计算参考分 |
