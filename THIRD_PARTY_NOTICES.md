# 第三方来源与授权

代码、模型、素材适用各自授权。项目原创代码尚未单独授予开源许可证。

| 上游 | 用途 | 已记录许可 |
| --- | --- | --- |
| ComfyUI | 独立安装的推理引擎 | GPL-3.0 |
| Comfy workflow_templates | 原始与派生工作流 | MIT，文本见 third_party/workflow_templates-LICENSE |
| BFL Klein 4B FP8、Comfy 模型分包 | 生成模型、编码器和 VAE | 上游声明 Apache-2.0 |
| U²-Net | 分割架构 | Apache-2.0 |
| rembg | ONNX 发布与预处理参考 | 仓库 MIT，发布资源未附独立资源级许可文本 |

权重和 ComfyUI 代码通过安装器获取。React、Vite、Lucide 使用各自依赖包内 LICENSE，版本锁在 web/package-lock.json。

## 素材

- Comfy 官方示例输入的原始 URL、哈希与大小见 input/sources.json。
- 白杯照片 Mug of Tea，Factorylad，公共领域。实拍猫来自 Hugging Face documentation-images，数据集声明 CC BY-NC-SA 4.0，详见 input/additional_sources.json。
- 望江北门照片，Kcx36，CC BY-SA 4.0，经过裁切缩放，详见 input/scu_sources.json。
- 柴犬、O RLY? 鸮、《呐喊》的公共领域声明、署名与处理方式见 input/meme_sources.json。
- fine_raccoon.svg 及其 PNG 为原创浣熊端杯情境图。

来源清单应随素材保留，生成物涉及参考素材时应考虑相应授权条件。川大相关作品是课程概念打样，不代表校方官方文创或背书。

## 研究参考

交互与预设研究参考 Fooocus、InvokeAI、IOPaint、Memegen 与 OpenAI 官方图像提示示例，当前前端与提示目录由本项目实现。早期上游调研见 docs/upstream_research.json。
