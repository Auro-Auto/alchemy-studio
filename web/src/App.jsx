import React, { useCallback, useEffect, useRef, useState } from 'react';
import RegionEditor from './RegionEditor.jsx';
import Canvas from './Canvas.jsx';
import { ArrowLeftRight, ArrowRight, ArrowUpRight, Check, ChevronDown, Download, Expand, FileJson, History, ImagePlus, Layers, LoaderCircle, Maximize2, Minimize2, RefreshCw, ScanLine, Settings2, Square, TriangleAlert, Upload, X, ZoomIn } from 'lucide-react';

const RECIPES = [
  { id: 'ceramic', name: '陶瓷' },
  { id: 'sneaker', name: '球鞋' },
  { id: 'plush', name: '毛绒' },
  { id: 'crystal', name: '水晶' },
  { id: 'architecture', name: '建筑浮雕' },
  { id: 'meme', name: '梗图' },
  { id: 'custom', name: '自定义' },
];
const STYLES = [
  { id: 'original', name: '原样', group: '基础' },
  { id: 'comic', name: '漫画', group: '纸上' },
  { id: 'clay', name: '黏土', group: '立体' },
  { id: 'ink', name: '水墨', group: '纸上' },
  { id: 'pixel', name: '像素', group: '图形' },
  { id: 'poster', name: '复古海报', group: '图形' },
];
const FALLBACK_CREATIVE_OPTIONS = { styles: STYLES, instructions: [] };
const PRESET_CATEGORIES = [
  { id: 'all', name: '全部' },
  { id: 'campus', name: '川大' },
  { id: 'meme', name: '热梗' },
];
const FINISHED = new Set(['success', 'failed', 'cancelled']);
const STATUS_NAMES = { queued: '等待中', running: '生成中', success: '已完成', failed: '失败', cancelled: '已取消' };

function resolveStyle(value, styles) {
  const matches = (option) => option.id === value || option.name === value;
  return styles.find(matches) || STYLES.find(matches);
}

function optionList(values) {
  if (!Array.isArray(values)) return [];
  return values.filter((item) => item && typeof item.id === 'string' && item.id
    && typeof item.name === 'string' && item.name);
}

function groupOptions(options) {
  const groups = new Map();
  for (const option of options) {
    const name = typeof option.group === 'string' && option.group ? option.group : '其他';
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(option);
  }
  return [...groups];
}

async function api(path, options = {}, timeout = 25000) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`/api${path}`, { ...options, signal: controller.signal });
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { throw new Error('服务返回了无法解析的响应，请检查应用服务。'); }
    if (!response.ok) {
      const detail = data.detail || data.error || `请求失败（${response.status}）`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('连接超时，请检查服务或 SSH 隧道。');
    if (error instanceof TypeError) throw new Error('无法连接工作台服务，请检查服务与 SSH 隧道。');
    throw error;
  } finally { window.clearTimeout(timer); }
}

function imageFromId(id, known) {
  if (!id) return null;
  return known.current[id] || { id, url: `/api/files/${id.split('/').map(encodeURIComponent).join('/')}` };
}
function projectAssetUrl(path) {
  if (typeof path !== 'string' || !/^(input|output|data)\//.test(path) || path.split('/').some((part) => part === '..' || part === '.')) return null;
  return `/api/files/${path.split('/').map(encodeURIComponent).join('/')}`;
}
function timeLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function stageLabel(job) {
  if (!job) return '等待配方';
  if (job.status === 'success') return '生成完成';
  if (job.status === 'cancelled') return '任务已取消';
  if (job.status === 'failed') return '这次生成未完成';
  const stage = job.stage || '';
  if (/[\u4e00-\u9fff]/.test(stage)) return stage;
  const value = stage.toLowerCase();
  if (/cancel/.test(value)) return '等待当前阶段结束后取消';
  if (/segment|isolat|prepar|preprocess/.test(value)) return '整理原料与提纯主体';
  if (/rank|scor|finish|composit|postprocess/.test(value)) return '整理候选与处理背景';
  if (/generat|sampl|infer|render|comfy/.test(value)) return '融合形态与外观';
  if (/queue|wait/.test(value) || job.status === 'queued') return '已进入生成队列';
  return '正在执行配方';
}

function Photo({ src, alt, className = '', ...props }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  if (!src || failed) return <div className={`image-unavailable ${className}`} role="img" aria-label={alt}><ImagePlus size={24} /><span>{src ? '图片暂不可用' : '等待图片'}</span></div>;
  return <img src={src} alt={alt} className={className} decoding="async" onError={() => setFailed(true)} {...props} />;
}

function CreativePanel({ id, label, options, renderOption }) {
  return <div className="creative-panel" id={id} role="group" aria-label={label}>
    {groupOptions(options).map(([name, entries]) => <section className="creative-group" key={name} aria-label={name}>
      <h3>{name}</h3>
      <div className="creative-grid">{entries.map(renderOption)}</div>
    </section>)}
  </div>;
}

function StylePanel({ options, selected, disabled, onSelect }) {
  const [group, setGroup] = useState(null);
  const groups = new Map(groupOptions(options));
  const defaultGroup = selected.id === 'original' && groups.has('纸上') ? '纸上' : selected.group;
  const activeGroup = [group, defaultGroup, groups.keys().next().value].find((name) => groups.has(name));
  return <div className="creative-panel style-panel" id="creative-styles" role="group" aria-label="画风选项">
    <div className="style-groups" role="group" aria-label="画风分类">
      {[...groups.keys()].map((name) => <button key={name} aria-pressed={activeGroup === name}
        onClick={() => setGroup(name)}>{name}</button>)}
    </div>
    <div className="creative-grid style-grid" key={activeGroup}>
      {groups.get(activeGroup)?.map((option) => (
        <button key={option.id} className="creative-choice style-choice" aria-pressed={selected.id === option.id}
          disabled={disabled} onClick={() => onSelect(option.id)}>
          {typeof option.preview_url === 'string' && option.preview_url && <Photo className="creative-preview" src={option.preview_url} alt="" />}
          <span>{option.name}</span>{selected.id === option.id && <Check size={14} />}
        </button>
      ))}
    </div>
  </div>;
}

function UploadCard({ role, image, sourceUrl, uploading, disabled, onUpload }) {
  const input = useRef(null);
  const [dragging, setDragging] = useState(false);
  const select = () => !disabled && !uploading && input.current?.click();
  return <div className={`ingredient-card ${dragging ? 'dragging' : ''} ${uploading ? 'is-uploading' : ''}`}>
    <div className="ingredient-title" title={role === 'A' ? '保留这张图中物品的形状与用途' : '参考这张图中主体的外观与配色'}>
      <span className="role-badge">{role}</span><strong>{role === 'A' ? '原图' : '参考图'}</strong>
      {sourceUrl && <a className="ingredient-source" href={sourceUrl} target="_blank" rel="noreferrer">素材出处</a>}
    </div>
    <div className={`drop-zone ${image ? 'has-image' : ''}`} role="button" tabIndex={disabled ? -1 : 0} aria-label={`上传${role === 'A' ? '结构' : '灵感'}原料 ${role}`} aria-disabled={disabled || uploading}
      onClick={select} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } }}
      onDragOver={(event) => { event.preventDefault(); if (!disabled) setDragging(true); }} onDragLeave={() => setDragging(false)}
      onDrop={(event) => { event.preventDefault(); setDragging(false); if (!disabled && !uploading && event.dataTransfer.files[0]) onUpload(event.dataTransfer.files[0]); }}>
      {image ? <Photo src={image.url} alt={`${role} ${role === 'A' ? '结构参考图' : '外观参考图'}`} /> : <div className="upload-empty"><ImagePlus size={25} strokeWidth={1.4} /><span>选择图片</span></div>}
      {image && <span className="replace-label" aria-hidden="true" title="更换图片"><Upload size={14} /></span>}
      {uploading && <div className="upload-loading"><LoaderCircle className="spin" size={22} /><span>上传中</span></div>}
    </div>
    <input ref={input} type="file" accept="image/*" aria-label={`选择原料 ${role} 文件`} className="visually-hidden" disabled={disabled || uploading} onChange={(event) => { if (event.target.files[0]) onUpload(event.target.files[0]); event.target.value = ''; }} />
  </div>;
}

function Switch({ checked, onChange, disabled, title, children }) {
  return <label className={`switch-row ${disabled ? 'disabled' : ''}`} title={title}>
    <span>{children}</span><input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><span className="switch-track" aria-hidden="true"><span /></span>
  </label>;
}

function Modal({ image, onClose }) {
  const container = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    container.current?.focus();
    const escape = (event) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('keydown', escape); previous?.focus?.(); };
  }, [onClose]);
  return <div className="modal-backdrop" onClick={onClose}><div className="image-modal" role="dialog" aria-modal="true" aria-label={image.title} tabIndex={-1} ref={container} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => { if (event.key === 'Tab') { const links = container.current.querySelectorAll('button, a[href]'); const first = links[0]; const last = links[links.length - 1]; if (event.shiftKey && (document.activeElement === first || document.activeElement === container.current)) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } } }}>
    <div className="modal-header"><span>{image.title}</span><div><a className="icon-button" href={image.downloadUrl || image.url} download aria-label="下载当前图片"><Download size={18} /></a><button className="icon-button" onClick={onClose} aria-label="关闭图片"><X size={20} /></button></div></div>
    <Photo src={image.url} alt={image.title} />
  </div></div>;
}

export default function App() {
  const [status, setStatus] = useState(null);
  const [connectionError, setConnectionError] = useState('');
  const [checking, setChecking] = useState(true);
  const [presets, setPresets] = useState([]);
  const [presetCategory, setPresetCategory] = useState('all');
  const [presetId, setPresetId] = useState('');
  const [preview, setPreview] = useState(null);
  const [images, setImages] = useState({ a: null, b: null });
  const [uploads, setUploads] = useState({ a: false, b: false });
  const [recipe, setRecipe] = useState('ceramic');
  const [style, setStyle] = useState('original');
  const [creativeOptions, setCreativeOptions] = useState(FALLBACK_CREATIVE_OPTIONS);
  const [loadingCreativeOptions, setLoadingCreativeOptions] = useState(true);
  const [creativePanel, setCreativePanel] = useState(null);
  const [mode, setMode] = useState('structured');
  const [prompt, setPrompt] = useState('');
  const [isolate, setIsolate] = useState(false);
  const [background, setBackground] = useState(false);
  const [candidates, setCandidates] = useState(2);
  const [seed, setSeed] = useState('42');
  const [megapixels, setMegapixels] = useState(0.5);
  const [job, setJob] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [selectedCandidate, setSelectedCandidate] = useState(0);
  const [resultVariant, setResultVariant] = useState('composite');
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [jobError, setJobError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [view, setView] = useState('studio');
  const [historyFilter, setHistoryFilter] = useState('all');
  const [canvasView, setCanvasView] = useState('result');
  const [trayChoice, setTrayChoice] = useState(null);
  const [demo, setDemo] = useState(false);
  const [modal, setModal] = useState(null);
  const [toast, setToast] = useState('');
  const [regionEditor, setRegionEditor] = useState(null);
  const [editRegions, setEditRegions] = useState([]);
  const [protectRegions, setProtectRegions] = useState([]);
  const [regionDetail, setRegionDetail] = useState(false);
  const knownAssets = useRef({});
  const initialized = useRef(false);
  const submittingRef = useRef(false);
  const uploadLocks = useRef({ a: false, b: false });
  const completionHandled = useRef('');
  const styleTrigger = useRef(null);
  const instructionTrigger = useRef(null);
  const promptInput = useRef(null);
  const running = !!job && !FINISHED.has(job.status);
  const busy = submitting || running;
  const uploading = uploads.a || uploads.b;
  const knownStyle = resolveStyle(style, creativeOptions.styles);
  const chosenStyle = knownStyle || STYLES[0];
  const pendingPresetStyle = loadingCreativeOptions && !knownStyle;
  const selected = job?.candidates?.[selectedCandidate] || job?.candidates?.[0];
  const finalImageId = selected?.provenance?.final_image;
  const finalImageUrl = projectAssetUrl(finalImageId);
  const refinementSource = job?.status === 'success' && images.b && finalImageUrl && finalImageUrl === selected?.url
    ? { id: finalImageId, url: finalImageUrl }
    : null;
  const editingResult = regionEditor === 'result';
  const editorImage = editingResult ? refinementSource : images.a;
  const detailMetadata = job?.region_control?.mode === 'detail' ? job.region_control.metadata : null;
  const hasComposite = selected?.metrics?.background_locked === true && !!selected.raw_url;
  const showRaw = hasComposite && resultVariant === 'raw';
  const resultLabel = selected ? showRaw ? '原始生成' : hasComposite ? '最终结果' : '生成结果' : '示例';
  const resultUrl = (showRaw ? selected.raw_url : selected?.url) || (!job ? preview?.url : null);
  const downloadUrl = selected ? showRaw ? selected.raw_download_url || selected.raw_url : selected.download_url || selected.url : resultUrl;
  const original = job ? imageFromId(job.config?.image_a, knownAssets) : images.a;
  const viewingOriginal = !!original && (canvasView === 'original' || (!resultUrl && !busy));
  const compare = canvasView === 'compare' && !!resultUrl && !!original;
  const canvasImage = viewingOriginal
    ? { url: original.url, label: '原图' }
    : resultUrl ? { url: resultUrl, label: resultLabel } : null;
  const canvasDownloadUrl = viewingOriginal ? original.url : downloadUrl;
  const activeTray = job?.candidates?.length
    ? trayChoice?.jobId === job.id ? trayChoice.value : 'candidates'
    : 'presets';
  const progress = Math.max(0, Math.min(100, Number(job?.progress) || 0));
  const closeModal = useCallback(() => setModal(null), []);
  const closeRegionEditor = useCallback(() => setRegionEditor(null), []);
  const clearRegions = useCallback(() => {
    setEditRegions([]);
    setProtectRegions([]);
    setRegionDetail(false);
    setRegionEditor(null);
  }, []);

  const applyPreset = useCallback((preset) => {
    knownAssets.current[preset.image_a.id] = preset.image_a;
    knownAssets.current[preset.image_b.id] = preset.image_b;
    setImages({ a: preset.image_a, b: preset.image_b });
    setRecipe(preset.recipe || 'ceramic'); setPrompt(preset.prompt || '');
    setStyle(preset.style || 'original');
    setCreativePanel(null);
    if (preset.mode === 'structured' || preset.mode === 'baseline') setMode(preset.mode);
    if (typeof preset.isolate_subject === 'boolean') setIsolate(preset.isolate_subject);
    if (typeof preset.lock_background === 'boolean') setBackground(preset.lock_background);
    if (preset.megapixels === 0.5 || preset.megapixels === 1) setMegapixels(preset.megapixels);
    if (Number.isInteger(preset.candidates) && preset.candidates >= 1 && preset.candidates <= 3) setCandidates(preset.candidates);
    setPresetId(preset.id); setPreview(preset.preview_url ? { url: preset.preview_url, title: preset.title } : null);
    setJob(null); setJobError(''); setCanvasView('result'); setSelectedCandidate(0); setView('studio');
    setEditRegions(preset.edit_regions || []);
    setProtectRegions(preset.protect_regions || []);
    setRegionDetail(!!preset.edit_regions?.length && preset.region_detail === true);
    setRegionEditor(null);
    if (Number.isSafeInteger(preset.seed)) setSeed(String(preset.seed));
  }, []);

  const refreshStatus = useCallback(async () => {
    try { const data = await api('/status', {}, 8000); setStatus(data); setConnectionError(''); }
    catch (error) { setStatus(null); setConnectionError(error.message); }
    finally { setChecking(false); }
  }, []);

  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true);
    try { const data = await api('/jobs?compact=true', {}, 8000); setJobs(Array.isArray(data) ? data : []); setHistoryError(''); }
    catch (error) { setHistoryError(error.message); }
    finally { setHistoryLoading(false); }
  }, []);

  const loadPresets = useCallback(async () => {
    try {
      const data = await api('/presets', {}, 8000);
      if (!Array.isArray(data)) throw new Error('样例数据格式不正确');
      setPresets(data);
      data.forEach((item) => { knownAssets.current[item.image_a.id] = item.image_a; knownAssets.current[item.image_b.id] = item.image_b; });
      if (!initialized.current && data.length) { initialized.current = true; applyPreset(data[0]); }
    } catch (error) { setConnectionError(error.message); }
  }, [applyPreset]);

  const loadCreativeOptions = useCallback(async () => {
    setLoadingCreativeOptions(true);
    try {
      const data = await api('/creative-options', {}, 8000);
      const styles = new Map(optionList(data.styles).map((option) => [option.id, option]));
      for (const option of STYLES) if (!styles.has(option.id)) styles.set(option.id, option);
      setCreativeOptions({
        styles: [...styles.values()],
        instructions: optionList(data.instructions).filter((option) => typeof option.prompt === 'string' && option.prompt.trim()),
      });
    } catch {
      setCreativeOptions(FALLBACK_CREATIVE_OPTIONS);
    } finally {
      setLoadingCreativeOptions(false);
    }
  }, []);

  useEffect(() => {
    refreshStatus(); loadPresets(); refreshHistory(); loadCreativeOptions();
    const timer = window.setInterval(refreshStatus, 10000);
    return () => window.clearInterval(timer);
  }, [refreshStatus, loadPresets, refreshHistory, loadCreativeOptions]);
  useEffect(() => { if (status && !initialized.current) loadPresets(); }, [status, loadPresets]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 4500); return () => window.clearTimeout(timer); }, [toast]);
  useEffect(() => setResultVariant('composite'), [job?.id, selected?.id]);
  useEffect(() => {
    const onEscape = (event) => { if (event.key === 'Escape') setDemo(false); };
    const onFullscreen = () => { if (!document.fullscreenElement) setDemo(false); };
    document.addEventListener('keydown', onEscape); document.addEventListener('fullscreenchange', onFullscreen);
    return () => { document.removeEventListener('keydown', onEscape); document.removeEventListener('fullscreenchange', onFullscreen); };
  }, []);

  useEffect(() => {
    if (!job?.id || FINISHED.has(job.status)) return;
    let stopped = false;
    let timer;
    const poll = async () => {
      try {
        const data = await api(`/jobs/${encodeURIComponent(job.id)}`, {}, 10000);
        if (stopped) return;
        setJob(data); setJobError('');
        if (FINISHED.has(data.status)) { setCancelling(false); refreshHistory(); refreshStatus(); return; }
      } catch (error) { if (!stopped) setJobError(`进度连接暂时中断：${error.message} 服务端任务可能仍在运行。`); }
      if (!stopped) timer = window.setTimeout(poll, 1200);
    };
    timer = window.setTimeout(poll, 400);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [job?.id, job?.status, refreshHistory, refreshStatus]);

  useEffect(() => {
    if (job?.status === 'success' && completionHandled.current !== job.id) {
      completionHandled.current = job.id;
      setSelectedCandidate(Math.max(0, Math.min(job.best_index || 0, (job.candidates?.length || 1) - 1)));
    }
  }, [job]);

  async function upload(role, file) {
    if (uploadLocks.current[role] || busy) return;
    if (!file.type.startsWith('image/') && !/\.(png|jpe?g|webp|bmp|tiff?|gif|avif)$/i.test(file.name)) { setToast('请选择图片文件，例如 PNG、JPG 或 WebP。'); return; }
    uploadLocks.current[role] = true;
    setUploads((old) => ({ ...old, [role]: true })); setJobError('');
    try {
      const body = new FormData(); body.append('file', file);
      const asset = await api('/uploads', { method: 'POST', body }, 120000);
      knownAssets.current[asset.id] = asset;
      initialized.current = true;
      setImages((old) => ({ ...old, [role]: asset })); setPresetId(''); setPreview(null); setJob(null); setCanvasView('result');
      if (role === 'a') clearRegions();
    } catch (error) { setToast(error.message); }
    finally { uploadLocks.current[role] = false; setUploads((old) => ({ ...old, [role]: false })); }
  }

  async function generate() {
    if (submittingRef.current || busy || uploading || pendingPresetStyle) return;
    if (!status?.comfy_connected) { setToast('生成服务尚未连接，请先检查连接状态。'); return; }
    if (!images.a || !images.b) { setToast('先放入两张原料图片。'); return; }
    const seedValue = Number(seed);
    if (!/^\d+$/.test(seed) || !Number.isSafeInteger(seedValue) || seedValue < 0) { setToast('种子请输入 0 到 9007199254740991 之间的整数。'); return; }
    if ((isolate || (background && !editRegions.length)) && !status.segmentation_available) { setToast('主体分割模型尚未就绪，请关闭主体提纯和背景保留。'); return; }
    const config = {
      image_a: images.a.id, image_b: images.b.id, recipe, style: chosenStyle.id, mode, prompt: prompt.trim(),
      isolate_subject: isolate, lock_background: background, candidates, seed: seedValue, megapixels,
      edit_mode: editRegions.length ? 'region' : 'global',
      edit_regions: editRegions,
      protect_regions: editRegions.length ? protectRegions : [],
      region_detail: editRegions.length > 0 && regionDetail,
    };
    submittingRef.current = true; setSubmitting(true); setJobError(''); setCanvasView('result'); setCreativePanel(null);
    try {
      const created = await api('/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
      setJob({ id: created.id, status: 'queued', stage: 'queued', progress: 0, config, candidates: [], preprocessing: {}, created_at: new Date().toISOString() });
      setSelectedCandidate(0); setPreview(null); setView('studio'); refreshHistory();
    } catch (error) { setJobError(`${error.message} 若提交时连接中断，请先查看实验记录，确认是否已创建任务。`); refreshHistory(); }
    finally { submittingRef.current = false; setSubmitting(false); }
  }

  async function cancelJob() {
    if (!job || cancelling) return;
    setCancelling(true);
    try { const data = await api(`/jobs/${encodeURIComponent(job.id)}/cancel`, { method: 'POST' }); setJob(data); setToast('已请求取消，将在安全的阶段边界停止。'); if (FINISHED.has(data.status)) { setCancelling(false); refreshHistory(); } }
    catch (error) { setJobError(error.message); setCancelling(false); }
  }

  async function openJob(item) {
    if (busy || uploading) { setToast('当前实验结束后，可回看其他实验。'); return; }
    try {
      const data = await api(`/jobs/${encodeURIComponent(item.id)}`);
      const config = data.config;
      setImages({ a: imageFromId(config.image_a, knownAssets), b: imageFromId(config.image_b, knownAssets) });
      setRecipe(config.recipe); setMode(config.mode); setPrompt(config.prompt || ''); setIsolate(!!config.isolate_subject); setBackground(!!config.lock_background);
      setStyle(config.style || 'original');
      setCreativePanel(null);
      setCandidates(config.candidates); setSeed(String(config.seed)); setMegapixels(config.megapixels);
      setJob(data); setPreview(null); setPresetId(''); setSelectedCandidate(data.best_index || 0); setJobError(''); setCanvasView('result'); setView('studio');
      setEditRegions(config.edit_mode === 'region' ? config.edit_regions || [] : []);
      setProtectRegions(config.edit_mode === 'region' ? config.protect_regions || [] : []);
      setRegionDetail(config.edit_mode === 'region' && !!config.edit_regions?.length && config.region_detail === true);
    } catch (error) { setToast(error.message); }
  }

  async function toggleDemo() {
    if (demo) { setDemo(false); if (document.fullscreenElement) await document.exitFullscreen().catch(() => {}); }
    else { setView('studio'); setDemo(true); window.scrollTo({ top: 0, behavior: 'smooth' }); if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen().catch(() => {}); }
  }

  function saveRegionSelection(edits, protects) {
    if (editingResult) {
      if (!refinementSource) return;
      knownAssets.current[refinementSource.id] = refinementSource;
      setImages((current) => ({ ...current, a: refinementSource }));
      setStyle(job.config.style || 'original');
      setCreativePanel(null);
      setPresetId('');
      setPreview(null);
      setJob(null);
      setJobError('');
      setSelectedCandidate(0);
      setCanvasView('result');
      setRegionDetail(true);
    }
    setEditRegions(edits);
    setProtectRegions(protects);
    closeRegionEditor();
  }

  function closeCreativePanelOnEscape(event) {
    if (!creativePanel || event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    setCreativePanel(null);
    (creativePanel === 'styles' ? styleTrigger : instructionTrigger).current?.focus();
  }

  function appendInstruction(instruction) {
    const addition = instruction.prompt.trim();
    if (prompt.includes(addition)) return;
    const separator = prompt && !prompt.endsWith('\n') ? '\n' : '';
    const next = prompt + separator + addition;
    if ([...next].length > 6000) { setToast('生成指令超过 6000 字，请先精简。'); return; }
    setPrompt(next);
    if (RECIPES.some((recipe) => recipe.id === instruction.recipe)) setRecipe(instruction.recipe);
    setCreativePanel(null);
    window.requestAnimationFrame(() => {
      promptInput.current?.focus();
      promptInput.current?.setSelectionRange(next.length, next.length);
    });
  }

  const filteredPresets = presetCategory === 'all' ? presets : presets.filter((preset) => preset.category === presetCategory);
  const filteredJobs = jobs.filter((item) => historyFilter === 'all' || (historyFilter === 'success' ? item.status === 'success' : !FINISHED.has(item.status)));
  const preprocessing = Object.entries(job?.preprocessing || {}).filter(([key, value]) => key.endsWith('_url') && value);
  const compositeMaskUrl = selected?.metrics?.background_locked === true
    ? projectAssetUrl(selected.metrics.composite_mask_path) || projectAssetUrl(selected.provenance?.finishing?.mask_path)
    : null;
  if (compositeMaskUrl) preprocessing.push(['editable_region_url', compositeMaskUrl]);
  if (selected?.raw_window_url) preprocessing.push(['raw_window_url', selected.raw_window_url]);
  const preLabels = { a_url: 'A · 处理后', b_url: 'B · 处理后', mask_a_url: 'A · 主体蒙版', mask_b_url: 'B · 主体蒙版', editable_region_url: '可编辑区域', edit_mask_url: '修改区域', latent_mask_url: '采样蒙版', detail_a_url: '局部采样窗口', raw_window_url: '原始窗口' };
  const referenceSourceUrl = presets.find((preset) => preset.image_b.id === images.b?.id
    && /^\/api\/files\/input\/[\w.-]+\.json$/.test(preset.reference_source_url))?.reference_source_url;
  const connected = !!status?.comfy_connected;

  return <div className={`app ${demo ? 'demo-mode' : ''}`}>
    <header className="site-header">
      <a className="brand" href="#" onClick={(event) => { event.preventDefault(); setView('studio'); }}><img className="brand-mark" src="/favicon.svg" alt="" />万物炼金台</a>
      <nav className="main-nav" aria-label="主导航">
        <button className={view === 'studio' ? 'active' : ''} onClick={() => setView('studio')}>生成</button>
        <button className={view === 'history' ? 'active' : ''} onClick={() => { setView('history'); refreshHistory(); }}>实验记录</button>
      </nav>
      <div className="header-actions"><span className={`connection-badge ${connected ? 'connected' : ''}`} title={status?.gpu || connectionError || '正在检测服务'}><i />{checking ? '连接中' : connected ? '已连接' : '未连接'}</span><button className={`demo-button ${demo ? 'active' : ''}`} aria-label={demo ? '退出演示' : '演示模式'} onClick={toggleDemo}>{demo ? <Minimize2 size={17} /> : <Maximize2 size={17} />}<span>{demo ? '退出演示' : '演示模式'}</span></button></div>
    </header>

    <main className={view === 'studio' ? 'studio-main' : 'history-main'}>
      {(connectionError || (!checking && status && !status.comfy_connected)) && <div className="connection-notice" role="status"><TriangleAlert size={17} /><span>{connectionError || '生成服务未就绪。'}</span><button onClick={() => { setChecking(true); refreshStatus(); loadPresets(); refreshHistory(); loadCreativeOptions(); }}><RefreshCw size={15} />重新连接</button></div>}

      {view === 'studio' ? <div className="workspace">
        <section className="result-column" aria-label="图像画布">
          <div className="result-panel">
            <div className="result-heading">
              <div className="canvas-modes" role="group" aria-label="画布视图">
                <button aria-pressed={viewingOriginal} disabled={!original} onClick={() => setCanvasView('original')}>原图</button>
                <button aria-pressed={!viewingOriginal && !compare} disabled={!resultUrl} onClick={() => setCanvasView('result')}>结果</button>
                <button aria-label="前后对比" title="前后对比" aria-pressed={compare} disabled={!resultUrl || !original}
                  onClick={() => setCanvasView(compare ? 'result' : 'compare')}><ArrowLeftRight size={15} /><span>前后对比</span></button>
              </div>
              <div className="canvas-tools">
                {hasComposite && !viewingOriginal && <div className="result-variants" role="group" aria-label="生成处理阶段">
                  <button className={showRaw ? 'selected' : ''} aria-pressed={showRaw} onClick={() => setResultVariant('raw')}
                    title={selected.raw_window_url ? '将原始生成窗口放回原图位置，尚未融合' : undefined}>原始生成</button>
                  <button className={!showRaw ? 'selected' : ''} aria-pressed={!showRaw} onClick={() => setResultVariant('composite')}>最终结果</button>
                </div>}
                {refinementSource && !viewingOriginal && !showRaw && !demo && <button className="region-open-button"
                  disabled={busy || uploading} onClick={() => setRegionEditor('result')}>重炼此图</button>}
                <button className="icon-button" disabled={!canvasImage} onClick={() => setModal({ url: canvasImage.url, downloadUrl: canvasDownloadUrl, title: canvasImage.label })}
                  aria-label={viewingOriginal ? '放大原图' : '放大结果'} title="全图预览"><Expand size={17} /></button>
                {canvasDownloadUrl && <a className="download-button" href={canvasDownloadUrl} download
                  aria-label={viewingOriginal ? '下载原图' : selected ? `下载${resultLabel}图片` : '保存样例'} title="下载当前图片"><Download size={16} /><span>下载</span></a>}
              </div>
            </div>
            <Canvas key={canvasImage?.url} Photo={Photo} image={canvasImage} original={original}
              compare={compare} busy={busy} checking={checking} emptyText={images.a ? '生成后在这里查看结果' : '上传图片或选择示例'} />
          </div>

          <section className="filmstrip" aria-label="图片胶卷">
            <div className="filmstrip-heading">
              <div className="tray-tabs" role="group" aria-label="胶卷内容">
                <button aria-pressed={activeTray === 'presets'} onClick={() => setTrayChoice({ jobId: job?.id, value: 'presets' })}>示例</button>
                <button aria-pressed={activeTray === 'candidates'} disabled={!job?.candidates?.length}
                  onClick={() => setTrayChoice({ jobId: job?.id, value: 'candidates' })}>候选{job?.candidates?.length ? ` ${job.candidates.length}` : ''}</button>
              </div>
              {activeTray === 'presets' && presets.length > 0 && <div className="preset-categories" role="group" aria-label="素材分类">
                {PRESET_CATEGORIES.map(({ id, name }) => <button key={id} aria-pressed={presetCategory === id}
                  onClick={() => setPresetCategory(id)}>{name}</button>)}
              </div>}
            </div>
            {activeTray === 'candidates'
              ? <div className="filmstrip-items candidate-grid">{job.candidates.map((candidate, index) => (
                <button className={`candidate-card ${selectedCandidate === index ? 'selected' : ''}`} key={candidate.id}
                  onClick={() => { setSelectedCandidate(index); if (viewingOriginal) setCanvasView('result'); }}
                  aria-label={`查看候选 ${index + 1}`} aria-pressed={selectedCandidate === index}>
                  <div className="filmstrip-photo"><Photo src={candidate.url} alt={`候选 ${index + 1}`} fetchPriority="low" />{selectedCandidate === index && <span className="candidate-check"><Check size={12} /></span>}</div>
                  <span>候选 {index + 1}</span>
                </button>
              ))}</div>
              : filteredPresets.length
                ? <div className="filmstrip-items preset-grid">{filteredPresets.map((preset) => (
                  <button className={`preset-card ${presetId === preset.id ? 'selected' : ''}`} key={preset.id}
                    onClick={() => applyPreset(preset)} disabled={busy || uploading} aria-pressed={presetId === preset.id}>
                    <div className="filmstrip-photo"><Photo src={preset.preview_url} alt="" fetchPriority="low" /></div>
                    <span>{preset.title}</span>
                  </button>
                ))}</div>
                : <div className="preset-empty">
                  {checking ? <LoaderCircle size={16} className="spin" /> : presets.length ? '暂无此类示例'
                    : <button className="text-button" onClick={loadPresets}>重新载入示例</button>}
                </div>}
          </section>
        </section>

        <aside className="control-column" aria-label="生成设置">
          <div className="inspector-body">
            <div className="project-heading">
              <h1>{presets.find((preset) => preset.id === presetId)?.title || '创作台'}</h1>
            </div>
            <section className="assets-column" aria-label="素材">
            <div className="section-heading"><h2>素材</h2>
              <button className="text-button" disabled={!images.a || !images.b || busy || uploading} onClick={() => {
                setImages({ a: images.b, b: images.a });
                setPresetId(''); setPreview(null); setJob(null); setCanvasView('result'); clearRegions();
              }}><ArrowLeftRight size={15} />交换</button>
            </div>
            <div className="ingredients-grid">
              <UploadCard role="A" image={images.a} uploading={uploads.a} disabled={busy} onUpload={(file) => upload('a', file)} />
              <UploadCard role="B" image={images.b} sourceUrl={referenceSourceUrl} uploading={uploads.b} disabled={busy} onUpload={(file) => upload('b', file)} />
            </div>
            <div className="region-action-row">
              <button className={`region-open-button ${editRegions.length ? 'selected' : ''}`} aria-label="限定修改区域" aria-pressed={!!editRegions.length}
                disabled={!images.a || busy || uploading} onClick={() => setRegionEditor('source')}>
                <ScanLine size={16} />{editRegions.length ? '已限定修改区域' : '限定修改区域'}
              </button>
              {editRegions.length > 0 && <>
                <button className={`region-open-button ${regionDetail ? 'selected' : ''}`}
                  aria-pressed={regionDetail} title="用相同像素预算，集中处理修改区及周围画面"
                  disabled={busy || uploading} onClick={() => setRegionDetail((current) => !current)}>
                  <ZoomIn size={15} />局部高清
                </button>
                <button className="icon-button" aria-label="取消区域限定" title="恢复整图生成"
                  disabled={busy || uploading} onClick={clearRegions}><X size={16} /></button>
              </>}
            </div>
            </section>
            <section className="panel recipe-panel" onKeyDown={closeCreativePanelOnEscape}>
              <div className="recipe-options" role="group" aria-label="材质与类型">{RECIPES.map(({ id, name }) => <button key={id} className={`recipe-chip ${recipe === id ? 'selected' : ''}`} aria-pressed={recipe === id} disabled={busy} onClick={() => setRecipe(id)}>{name}</button>)}</div>
              <div className="style-field">
                <span>风格</span>
                <button className="style-trigger" ref={styleTrigger} aria-label={`选择风格：${chosenStyle.name}`}
                  aria-expanded={creativePanel === 'styles'} aria-controls="creative-styles" disabled={busy}
                  onClick={() => setCreativePanel((current) => current === 'styles' ? null : 'styles')}>
                  <span>{chosenStyle.name}</span><ChevronDown size={16} />
                </button>
              </div>
              {creativePanel === 'styles' && <StylePanel options={creativeOptions.styles} selected={chosenStyle}
                disabled={busy} onSelect={(id) => { setStyle(id); setCreativePanel(null); styleTrigger.current?.focus(); }} />}
              <div className="prompt-heading">
                <label className="prompt-label" htmlFor="extra-prompt">生成指令</label>
                {creativeOptions.instructions.length > 0 && <button className="text-button" ref={instructionTrigger}
                  aria-expanded={creativePanel === 'instructions'} aria-controls="creative-instructions" disabled={busy}
                  onClick={() => setCreativePanel((current) => current === 'instructions' ? null : 'instructions')}>
                  常用指令 <ChevronDown size={14} />
                </button>}
              </div>
              {creativePanel === 'instructions' && <CreativePanel id="creative-instructions" label="指令选项"
                options={creativeOptions.instructions} renderOption={(option) => (
                  <button key={option.id} className="creative-choice" title={option.prompt}
                    disabled={busy || prompt.includes(option.prompt.trim())} onClick={() => appendInstruction(option)}>{option.name}</button>
                )} />}
              <textarea id="extra-prompt" ref={promptInput} value={prompt} disabled={busy} onChange={(event) => setPrompt(event.target.value)} placeholder="描述你想生成的物品" rows={4} />
              <details className="advanced-options"><summary><Settings2 size={16} />更多设置<ChevronDown size={16} /></summary><div className="advanced-body">
                <div className="vision-options"><Switch checked={isolate} onChange={setIsolate} disabled={busy || (!status?.segmentation_available && !isolate)} title={!status?.segmentation_available ? '分割模型未就绪；已开启的选项仍可关闭' : '分离参考图背景，减少背景干扰'}><ScanLine size={16} />主体提纯</Switch><Switch checked={background} onChange={setBackground} disabled={busy || !!editRegions.length || (!status?.segmentation_available && !background)} title={editRegions.length ? '当前使用手绘区域控制，区域以外保持原样' : '与主体提纯同时开启时，保留原图 A 场景、提纯参考图 B；使用主体蒙版合成'}><Layers size={16} />保留原图背景</Switch></div>
                <div className="field"><span id="mode-label">融合方式</span><div className="segmented" role="group" aria-labelledby="mode-label"><button className={mode === 'structured' ? 'selected' : ''} disabled={busy} onClick={() => setMode('structured')} title="指定原料角色、融合部位与材质">结构化配方</button><button className={mode === 'baseline' ? 'selected' : ''} disabled={busy} onClick={() => setMode('baseline')} title="使用简单融合指令">基础对照</button></div></div>
                <div className="form-row"><label>候选数量<select aria-label="候选数量" value={candidates} disabled={busy} onChange={(event) => setCandidates(Number(event.target.value))}><option value={1}>1 张</option><option value={2}>2 张</option><option value={3}>3 张</option></select></label><label>像素预算<select aria-label="像素预算" value={megapixels} disabled={busy} onChange={(event) => setMegapixels(Number(event.target.value))}><option value={0.5}>0.5 MP</option><option value={1}>1.0 MP</option></select></label></div>
                <label className="seed-field">随机种子<div><input inputMode="numeric" value={seed} disabled={busy} onChange={(event) => setSeed(event.target.value)} aria-label="随机种子" /><button className="icon-button" disabled={busy} onClick={() => setSeed(String(crypto.getRandomValues(new Uint32Array(1))[0]))} aria-label="更换随机种子" title="更换随机种子"><RefreshCw size={15} /></button></div></label>
              </div></details>
            </section>
          </div>

          <div className="inspector-footer">
            {(job || jobError || submitting) && <section className={`progress-panel ${job?.status === 'failed' ? 'has-error' : ''}`} aria-live="polite">
              <div className="progress-top">
                <div>{busy ? <LoaderCircle className="spin" size={16} /> : job?.status === 'success' ? <Check size={16} /> : <TriangleAlert size={16} />}
                  <strong>{submitting ? '正在提交' : stageLabel(job)}</strong>
                  {!busy && job?.elapsed_seconds != null && <span>{Number(job.elapsed_seconds).toFixed(1)} 秒</span>}
                </div>
                {!running && job && <a className="icon-button" href={`/api/jobs/${encodeURIComponent(job.id)}/export`} download aria-label="导出 JSON" title="导出 JSON"><FileJson size={16} /></a>}
              </div>
              {busy && <div className="progress-track" role="progressbar" aria-label="生成进度" aria-valuenow={Math.round(progress)}><span style={{ width: `${progress}%` }} /></div>}
              {(job?.error || jobError) && <p className="error-message" role="alert">{job?.error || jobError}</p>}
            </section>}
            {running ? <button className="secondary-button cancel-button" onClick={cancelJob} disabled={cancelling}><Square size={13} />{cancelling ? '等待取消' : '取消任务'}</button>
              : <button className="generate-button" onClick={generate} disabled={!connected || !images.a || !images.b || busy || uploading || pendingPresetStyle}>
                {submitting && <LoaderCircle className="spin" size={17} />}{submitting ? '正在提交' : '生成图片'}
              </button>}
          </div>
          <div className="inspector-details">
            {selected && <details className="result-details"><summary>结果详情<ChevronDown size={16} /></summary>
              <dl>
                <div><dt>随机种子</dt><dd>{selected.seed}</dd></div>
                <div><dt>生成方式</dt><dd>{job.config.mode === 'baseline' ? '基础对照' : '结构化配方'}</dd></div>
                <div><dt>风格</dt><dd>{resolveStyle(job.config.style, creativeOptions.styles)?.name || job.config.style || '原样'}</dd></div>
                <div><dt>修改范围</dt><dd>{job.config.edit_mode === 'region' ? '限定区域' : '整图'}</dd></div>
                {detailMetadata?.inference_size?.length === 2 && <div><dt>局部采样尺寸</dt><dd>{detailMetadata.inference_size.join(' × ')}</dd></div>}
                {Number.isFinite(detailMetadata?.sampling_density_gain) && <div>
                  <dt title="相对同像素预算的整图区域采样">局部采样密度</dt><dd>{detailMetadata.sampling_density_gain.toFixed(2)}×</dd>
                </div>}
                {selected.score != null && <div><dt title="根据轮廓、配色和前景完整度估算">参考匹配分</dt><dd>{Number(selected.score).toFixed(2)}</dd></div>}
              </dl>
            </details>}
            {!!preprocessing.length && <details className="preprocess-panel"><summary><ScanLine size={16} />处理过程<ChevronDown size={16} /></summary>
              <div className="preprocess-images">{preprocessing.map(([key, url]) => (
                <button key={key} onClick={() => setModal({ url, title: preLabels[key] || key })}>
                  <Photo src={url} alt="" loading="lazy" /><span>{preLabels[key] || key}</span>
                </button>
              ))}</div>
            </details>}
          </div>
        </aside>
      </div> : <section className="history-view">
        <div className="history-heading"><h1>实验记录</h1><button className="secondary-button" disabled={historyLoading} onClick={refreshHistory}><RefreshCw size={16} className={historyLoading ? 'spin' : ''} />刷新</button></div>
        <div className="history-toolbar"><div className="history-filters">{[['all', '全部'], ['success', '已完成'], ['active', '进行中']].map(([value, label]) => <button key={value} className={historyFilter === value ? 'selected' : ''} onClick={() => setHistoryFilter(value)}>{label}</button>)}</div><span>{jobs.length} 次实验</span></div>
        {historyError && <div className="connection-notice" role="alert"><TriangleAlert size={16} />{historyError}</div>}
        {filteredJobs.length ? <div className="history-grid">{filteredJobs.map((item) => { const cover = item.candidates?.[item.best_index || 0] || item.candidates?.[0]; return <article className="history-card" key={item.id}><button className="history-cover" onClick={() => openJob(item)} disabled={busy || uploading} aria-label={`回看 ${item.id} 实验`}>{cover ? <Photo src={cover.url} alt="实验生成结果" loading="lazy" /> : <div className="history-no-image"><ImagePlus size={28} /><span>{STATUS_NAMES[item.status] || item.status}</span></div>}<span className={`job-status status-${item.status}`}>{STATUS_NAMES[item.status]}</span></button><div className="history-info"><div><h3>{RECIPES.find((entry) => entry.id === item.config.recipe)?.name || '自定义'}</h3><span>{timeLabel(item.created_at)}</span></div>{item.error && <p className="history-error" title={item.error}>{item.error}</p>}<div className="history-card-actions"><button className="text-button" disabled={busy || uploading} onClick={() => openJob(item)}>回看<ArrowUpRight size={15} /></button><a className="icon-button" href={`/api/jobs/${encodeURIComponent(item.id)}/export`} download aria-label={`导出实验 ${item.id} JSON`} title="导出实验 JSON"><FileJson size={17} /></a></div></div></article>; })}</div> : <div className="history-empty"><History size={30} strokeWidth={1.5} /><h3>{historyLoading ? '正在载入' : '暂无实验记录'}</h3><button className="secondary-button" onClick={() => setView('studio')}>开始生成<ArrowRight size={16} /></button></div>}
      </section>}
    </main>
    {toast && <div className="toast" role="status"><span>{toast}</span><button onClick={() => setToast('')} aria-label="关闭提示"><X size={16} /></button></div>}
    {modal && <Modal image={modal} onClose={closeModal} />}
    {regionEditor && editorImage && <RegionEditor image={editorImage}
      editRegions={editingResult ? [] : editRegions} protectRegions={editingResult ? [] : protectRegions}
      onClose={closeRegionEditor} onSave={saveRegionSelection} />}

  </div>;
}
