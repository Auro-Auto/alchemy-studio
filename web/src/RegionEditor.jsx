import React, { useEffect, useRef, useState } from 'react';
import { Undo2, X } from 'lucide-react';
import { pointInImage, rectangleBetween, visibleRectangle } from './regions.js';

function Rectangle({ rect, kind, draft = false }) {
  return <div className={`region-rectangle region-${kind} ${draft ? 'draft' : ''}`} style={{ left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${(rect[2] - rect[0]) * 100}%`, height: `${(rect[3] - rect[1]) * 100}%` }} />;
}

export default function RegionEditor({ image, editRegions, protectRegions, onSave, onClose }) {
  const [{ edit: edits, protect: protects, strokes }, setRegions] = useState(() => ({
    edit: editRegions.map((rect) => [...rect]),
    protect: protectRegions.map((rect) => [...rect]),
    strokes: [],
  }));
  const [tool, setTool] = useState('edit');
  const [drawing, setDrawing] = useState(null);
  const [natural, setNatural] = useState(null);
  const [area, setArea] = useState({ width: 1, height: 1 });
  const [message, setMessage] = useState('');
  const workspace = useRef(null);
  const dialog = useRef(null);
  const drag = useRef(null);
  const firstButton = useRef(null);
  const undoButton = useRef(null);

  useEffect(() => {
    const previous = document.activeElement;
    firstButton.current?.focus();
    const observer = new ResizeObserver((entries) => setArea({ width: entries[0].contentRect.width, height: entries[0].contentRect.height }));
    observer.observe(workspace.current);
    const keydown = (event) => { if (event.key === 'Escape') { event.preventDefault(); onClose(); } };
    document.addEventListener('keydown', keydown);
    return () => { observer.disconnect(); document.removeEventListener('keydown', keydown); previous?.focus?.(); };
  }, [onClose]);

  const ratio = natural ? natural.width / natural.height : 1;
  const width = Math.min(area.width, area.height * ratio);
  const height = width / ratio;
  const rectangles = tool === 'edit' ? edits : protects;

  function pointerDown(event) {
    if (!natural || (event.pointerType === 'mouse' && event.button !== 0) || drag.current) return;
    if (rectangles.length >= 12) { setMessage('每类最多绘制 12 个区域。'); return; }
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const start = pointInImage(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect());
    drag.current = { start, tool, pointerId: event.pointerId };
    setDrawing({ rect: [...start, ...start], tool });
    setMessage('');
  }

  function pointerMove(event) {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    const end = pointInImage(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect());
    setDrawing({ rect: rectangleBetween(drag.current.start, end), tool: drag.current.tool });
  }

  function pointerUp(event) {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const rect = rectangleBetween(drag.current.start, pointInImage(event.clientX, event.clientY, bounds));
    if (visibleRectangle(rect, bounds)) {
      const kind = drag.current.tool;
      const rounded = rect.map((value) => Number(value.toFixed(6)));
      setRegions((previous) => previous[kind].length >= 12 ? previous : {
        ...previous,
        [kind]: [...previous[kind], rounded],
        strokes: [...previous.strokes, kind],
      });
    }
    drag.current = null; setDrawing(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function undo() {
    if (drag.current || !strokes.length) return;
    if (strokes.length === 1 && document.activeElement === undoButton.current) firstButton.current?.focus();
    setRegions((previous) => {
      const kind = previous.strokes.at(-1);
      if (!kind) return previous;
      return { ...previous, [kind]: previous[kind].slice(0, -1), strokes: previous.strokes.slice(0, -1) };
    });
    setMessage('');
  }

  function clear() {
    setRegions({ edit: [], protect: [], strokes: [] });
    setDrawing(null);
    drag.current = null;
    setMessage('');
  }

  function handleKeyDown(event) {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      undo();
      return;
    }
    if (event.key !== 'Tab') return;
    const buttons = [...dialog.current.querySelectorAll('button:not(:disabled)')];
    const first = buttons[0]; const last = buttons[buttons.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  return <div className="modal-backdrop" onClick={onClose}>
    <section className="region-modal" role="dialog" aria-modal="true" aria-labelledby="region-title" ref={dialog} onClick={(event) => event.stopPropagation()} onKeyDown={handleKeyDown}>
      <div className="modal-header"><h2 id="region-title">限定修改区域</h2><button ref={firstButton} className="icon-button" aria-label="关闭区域编辑器" onClick={onClose}><X size={20} /></button></div>
      <div className="region-toolbar">
        <div role="group" aria-label="区域绘制工具"><button className={`region-tool ${tool === 'edit' ? 'selected' : ''}`} aria-label="修改区" aria-pressed={tool === 'edit'} onClick={() => setTool('edit')}><i className="edit-swatch" />修改区{edits.length > 0 && <span>{edits.length}</span>}</button><button className={`region-tool ${tool === 'protect' ? 'selected' : ''}`} aria-label="保护区" aria-pressed={tool === 'protect'} onClick={() => setTool('protect')}><i className="protect-swatch" />保护区{protects.length > 0 && <span>{protects.length}</span>}</button></div>
        <div className="region-edit-actions">
          <button ref={undoButton} className="text-button" aria-keyshortcuts="Control+Z Meta+Z" title="撤销最近一次框选" disabled={!strokes.length || !!drawing} onClick={undo}><Undo2 size={15} />撤销</button>
          <button className="text-button" onClick={clear}>清空</button>
        </div>
      </div>
      <div className="region-workspace" ref={workspace}>
        <div className={`region-board tool-${tool}`} style={{ width, height }} data-testid="region-canvas" aria-label="在原图上拖动绘制矩形" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={() => { drag.current = null; setDrawing(null); }}>
          <img src={image.url} alt="原图 A 区域编辑" draggable="false" onLoad={(event) => setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} onError={() => setMessage('原图加载失败，请关闭后重试。')} />
          {edits.map((rect, index) => <Rectangle key={`edit-${index}`} rect={rect} kind="edit" />)}
          {protects.map((rect, index) => <Rectangle key={`protect-${index}`} rect={rect} kind="protect" />)}
          {drawing && <Rectangle rect={drawing.rect} kind={drawing.tool} draft />}
        </div>
      </div>
      <div className="region-footer"><span role={message ? 'alert' : undefined}>{message || (!edits.length && !protects.length ? '在图片上拖动框选区域。' : '')}</span><div><button className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={!edits.length || !natural || !!drawing} onClick={() => onSave(edits, protects)}>完成</button></div></div>
    </section>
  </div>;
}
