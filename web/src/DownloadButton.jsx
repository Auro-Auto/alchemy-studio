import React, { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { Download } from 'lucide-react';
import './download.css';

const IDLE = { href: null, phase: 'idle', progress: null };
// A requested file belongs to the user's download, rather than the button's
// lifetime. Changing candidates or closing a preview only changes subscriptions.
const downloadJobs = new Map();
const downloadListeners = new Map();

function downloadKey(href) {
  if (!href) return '';
  try { return new URL(href, window.location.href).href; } catch { return href; }
}

function notifyDownload(key) {
  downloadListeners.get(key)?.forEach((notify) => notify());
}

function updateDownload(job, phase, progress = null) {
  job.snapshot = { href: job.href, phase, progress };
  notifyDownload(job.key);
}

function safeFilename(value) {
  if (typeof value !== 'string') return '';
  return value.split(/[\\/]/).pop()
    .replace(/[\p{Cc}\p{Cf}]/gu, '')
    .replace(/[<>:"|?*]/g, '_')
    .replace(/^[. ]+|[. ]+$/g, '')
    .slice(0, 200);
}

function headerFilename(header) {
  if (!header) return '';
  const parameter = (name) => {
    const match = header.match(new RegExp(`(?:^|;)\\s*${name}\\s*=\\s*(?:"((?:\\\\.|[^"])*)"|([^;]*))`, 'i'));
    return match ? (match[1] ?? match[2]).trim().replace(/\\(["\\])/g, '$1') : '';
  };
  const extended = parameter('filename\\*').match(/^UTF-8'[^']*'(.*)$/i);
  if (extended) {
    try {
      const filename = safeFilename(decodeURIComponent(extended[1]));
      if (filename) return filename;
    } catch { /* Fall back to the regular filename or URL. */ }
  }
  return safeFilename(parameter('filename'));
}

function downloadFilename(response, href) {
  const url = new URL(response.url || href, window.location.href);
  if (url.origin === window.location.origin) {
    const filename = headerFilename(response.headers.get('Content-Disposition'));
    if (filename) return filename;
  }
  let basename = new URL(href, window.location.href).pathname.split('/').pop() || '';
  try { basename = decodeURIComponent(basename); } catch { /* Keep the URL's original name. */ }
  return safeFilename(basename) || 'download';
}

function responseLength(response) {
  // Fetch exposes decoded bytes; an encoded Content-Length would not describe them.
  const encoding = response.headers.get('Content-Encoding');
  if (encoding && encoding.toLowerCase() !== 'identity') return null;
  const header = response.headers.get('Content-Length');
  if (!header || !/^\d+$/.test(header.trim())) return null;
  const length = Number(header);
  return Number.isSafeInteger(length) && length > 0 ? length : null;
}

function dispatchDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.hidden = true;
  try {
    document.body.appendChild(link);
    link.click();
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  } finally {
    link.remove();
  }
  // Disk writes have no browser completion signal. Keep the URL briefly so the
  // browser can take ownership, even if the initiating preview has been closed.
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function receiveDownload(job) {
  const chunks = [];
  let reader;
  let blob;
  try {
    const response = await fetch(job.href, { credentials: 'same-origin' });
    if (!response.ok) {
      // Never surface a server response body or stack trace in the workbench.
      const error = new Error('download-http-error');
      error.downloadMessage = response.status === 404
        ? '图片已不可用，请重新选择后重试。' : '下载失败，请稍后重试。';
      response.body?.cancel().catch(() => {});
      throw error;
    }

    let total = responseLength(response);
    let received = 0;
    if (response.body?.getReader) {
      reader = response.body.getReader();
      if (total) updateDownload(job, 'loading', 0);
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.byteLength;
        // Treat an inconsistent length as unknown rather than invent a percentage.
        if (total && received > total) {
          total = null;
          updateDownload(job, 'loading');
        } else if (total) {
          updateDownload(job, 'loading', received / total);
        }
      }
      if (total && received !== total) throw new Error('download-incomplete');
      blob = new Blob(chunks, { type: response.headers.get('Content-Type') || 'application/octet-stream' });
      chunks.length = 0;
    } else {
      // Non-streaming fetch implementations still give honest busy feedback.
      blob = await response.blob();
    }

    dispatchDownload(blob, downloadFilename(response, job.href));
    blob = null;
    updateDownload(job, 'done');
    job.feedbackTimer = window.setTimeout(() => {
      if (downloadJobs.get(job.key) === job) {
        downloadJobs.delete(job.key);
        notifyDownload(job.key);
      }
    }, 1400);
  } catch (error) {
    if (downloadJobs.get(job.key) === job) {
      downloadJobs.delete(job.key);
      notifyDownload(job.key);
    }
    // App-owned feedback remains useful after its preview button has unmounted.
    // Progress updates only reach components with a live store subscription.
    try { job.onError?.(error.downloadMessage || '连接中断，请检查网络后重试。'); }
    catch { /* A notification callback must not leave a rejected download task. */ }
  } finally {
    chunks.length = 0;
    blob = null;
    job.onError = null;
    reader?.releaseLock();
  }
}

function requestDownload(href, onError) {
  const key = downloadKey(href);
  const existing = downloadJobs.get(key);
  if (existing?.snapshot.phase === 'loading') return;
  window.clearTimeout(existing?.feedbackTimer);
  const job = { key, href: key, onError, snapshot: { href: key, phase: 'loading', progress: null } };
  downloadJobs.set(key, job);
  notifyDownload(key);
  // The task intentionally survives component unmount. Page unload still gives
  // the browser its normal opportunity to terminate outstanding network work.
  void receiveDownload(job);
}

export default function DownloadButton({
  href,
  label = '下载',
  'aria-label': ariaLabel,
  title,
  className = 'download-button',
  iconOnly = false,
  disabled = false,
  onError,
}) {
  const [pageHidden, setPageHidden] = useState(() => document.hidden);
  const key = downloadKey(href);
  const subscribe = useCallback((notify) => {
    let listeners = downloadListeners.get(key);
    if (!listeners) {
      listeners = new Set();
      downloadListeners.set(key, listeners);
    }
    listeners.add(notify);
    return () => {
      listeners.delete(notify);
      if (!listeners.size) downloadListeners.delete(key);
    };
  }, [key]);
  const getSnapshot = useCallback(() => downloadJobs.get(key)?.snapshot || IDLE, [key]);
  const currentState = useSyncExternalStore(subscribe, getSnapshot, () => IDLE);

  useEffect(() => {
    const visibilityChanged = () => setPageHidden(document.hidden);
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => {
      document.removeEventListener('visibilitychange', visibilityChanged);
    };
  }, []);

  const busy = currentState.phase === 'loading';
  const progress = currentState.progress;

  function handleClick(event) {
    if (disabled) {
      event.preventDefault();
      return;
    }
    // Keep context menus, new tabs and the browser's modified-click behavior native.
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (href) requestDownload(href, onError);
  }

  return <a href={href} download className={`${className} download-motion${iconOnly ? ' download-motion-icon-only' : ''}`}
    aria-label={ariaLabel || label} title={title} aria-busy={busy || undefined}
    aria-disabled={disabled || busy || undefined} onClick={handleClick}
    data-download-state={currentState.phase} data-download-indeterminate={busy && progress === null || undefined}
    data-page-hidden={pageHidden || undefined}>
    <span className="download-motion-icon" aria-hidden="true">
      <Download className="download-motion-arrow" size={16} strokeWidth={1.8} />
      <svg className="download-motion-ring" viewBox="0 0 20 20" fill="none">
        <circle className="download-motion-track" cx="10" cy="10" r="7.25" />
        <circle className="download-motion-meter" cx="10" cy="10" r="7.25" pathLength="100"
          strokeDasharray="100" strokeDashoffset={progress === null ? 73 : 100 - progress * 100} />
      </svg>
      <svg className="download-motion-check" viewBox="0 0 20 20" fill="none">
        <path d="m5.5 10 3 3 6-6" pathLength="1" />
      </svg>
    </span>
    {!iconOnly && <span className="download-motion-label">{label}</span>}
    {busy && <span className="download-motion-sr" role="progressbar" aria-label="下载进度"
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress === null ? undefined : Math.round(progress * 100)} />}
    <span className="download-motion-sr" role="status" aria-live="polite" aria-atomic="true">
      {busy ? '正在下载' : currentState.phase === 'done' ? '已交给浏览器下载' : ''}
    </span>
  </a>;
}
