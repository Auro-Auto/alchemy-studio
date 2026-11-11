import React, { useLayoutEffect, useRef, useState } from 'react';
import { ArrowLeftRight, ImagePlus, LoaderCircle, Minus, Plus } from 'lucide-react';

const ZOOM_LEVELS = [1, 1.25, 1.5, 2, 3];

export default function Canvas({ Photo, image, original, compare, busy, checking, emptyText }) {
  const viewport = useRef(null);
  const [area, setArea] = useState({ width: 0, height: 0 });
  const [loaded, setLoaded] = useState(null);
  const [zoomIndex, setZoomIndex] = useState(0);
  const [split, setSplit] = useState(50);
  const natural = loaded?.url === image?.url ? loaded : null;
  const scale = ZOOM_LEVELS[zoomIndex];
  const fit = natural ? Math.min(area.width / natural.width, area.height / natural.height, 1) : 1;
  const frameStyle = natural
    ? { width: Math.max(1, natural.width * fit * scale), height: Math.max(1, natural.height * fit * scale) }
    : undefined;

  useLayoutEffect(() => {
    const observer = new ResizeObserver(([entry]) => setArea(entry.contentRect));
    observer.observe(viewport.current);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    const node = viewport.current;
    node.scrollLeft = (node.scrollWidth - node.clientWidth) / 2;
    node.scrollTop = (node.scrollHeight - node.clientHeight) / 2;
  }, [zoomIndex, image?.url]);

  function measure(event) {
    const { naturalWidth: width, naturalHeight: height } = event.currentTarget;
    setLoaded({ url: image.url, width, height });
  }

  return <>
    <div className="result-canvas" ref={viewport}>
      {image ? <div className={`canvas-frame ${natural ? 'sized' : ''}`} style={frameStyle}>
        {compare && <Photo src={original.url} alt="原料 A 原图" className="canvas-image" draggable={false} />}
        <div className="canvas-layer" style={compare ? { clipPath: `inset(0 0 0 ${split}%)` } : undefined}>
          <Photo src={image.url} alt={image.label} className="canvas-image" onLoad={measure} draggable={false} fetchPriority="high" />
        </div>
        {compare && natural && <>
          <div className="compare-divider" style={{ left: `${split}%` }}><span><ArrowLeftRight size={15} /></span></div>
          {split > 12 && <span className="compare-label left">原图</span>}
          {split < 88 && <span className="compare-label right">{image.label}</span>}
          <input className="compare-slider" type="range" min="0" max="100" value={split}
            onChange={(event) => setSplit(Number(event.target.value))} aria-label="调整原图和结果对比位置" />
        </>}
      </div> : <div className="canvas-empty">
        {busy ? <LoaderCircle className="spin" size={28} /> : <ImagePlus size={32} strokeWidth={1.4} />}
        <p>{busy ? '正在生成图片' : checking ? '正在载入画布' : emptyText}</p>
      </div>}
    </div>
    <div className="canvas-status">
      <div className="canvas-caption">
        {image && <span>{image.title || image.label}</span>}
        {natural && <span className="canvas-dimensions">{natural.width} × {natural.height}</span>}
      </div>
      <div className="zoom-tools" role="group" aria-label="画布缩放">
        <button className="icon-button" aria-label="缩小画布" title="缩小画布" disabled={!natural || zoomIndex === 0} onClick={() => setZoomIndex(zoomIndex - 1)}><Minus size={15} /></button>
        <button className="zoom-fit" aria-label="适应画布" title="适应画布" disabled={!natural} onClick={() => setZoomIndex(0)}>{zoomIndex === 0 ? '适应' : `${Math.round(scale * 100)}%`}</button>
        <button className="icon-button" aria-label="放大画布" title="放大画布" disabled={!natural || zoomIndex === ZOOM_LEVELS.length - 1} onClick={() => setZoomIndex(zoomIndex + 1)}><Plus size={15} /></button>
      </div>
    </div>
  </>;
}
