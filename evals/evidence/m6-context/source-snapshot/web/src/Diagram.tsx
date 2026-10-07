import { useEffect, useId, useState } from 'react';

export function Diagram({ source, caption, fallback }: { source: string; caption: string; fallback: string }) {
  const identifier = useId().replace(/[^a-zA-Z0-9]/g, '');
  const [document, setDocument] = useState('');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let obsolete = false;
    setDocument(''); setFailed(false);
    void (async () => {
      // Config directives, clicks, external assets and authored CSS are outside
      // this diagram channel. The textual fallback is always retained.
      if (source.length > 6000 || /%%\s*\{|^\s*---|\b(?:click|style|classDef|linkStyle)\b|https?:|javascript:|data:|<[a-z!/]/im.test(source)) throw new Error('unsupported diagram capability');
      const [{ default: mermaid }, { default: purifier }] = await Promise.all([import('mermaid'), import('dompurify')]);
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true,
        maxTextSize: 6000, maxEdges: 50, htmlLabels: false, look: 'classic', theme: 'default',
        secure: ['secure', 'securityLevel', 'startOnLoad', 'maxTextSize', 'maxEdges', 'htmlLabels', 'look', 'theme', 'suppressErrorRendering', 'dompurifyConfig'] });
      const { svg } = await mermaid.render('diagram'+identifier, source);
      const cleaned = purifier.sanitize(svg, { USE_PROFILES: { svg: true },
        FORBID_TAGS: ['script', 'foreignObject', 'image', 'a', 'use', 'animate', 'set', 'iframe', 'filter'],
        FORBID_ATTR: ['href', 'xlink:href'] });
      const html = '<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;"><style>body{margin:0;background:#fafaf4}svg{display:block;max-width:100%;height:auto;margin:auto}</style>'+cleaned;
      if (!obsolete) setDocument(html);
    })().catch(() => { if (!obsolete) setFailed(true); });
    return () => { obsolete = true; };
  }, [source, identifier]);
  return <figure className="diagram-block">
    {document && <iframe title={caption || '知识图示'} className="diagram-frame" sandbox="" srcDoc={document} />}
    {!document && <p className="hint">{failed ? '图示暂时无法显示，请参考文字说明。' : '正在绘制图示…'}</p>}
    <figcaption>{caption}</figcaption><p className="hint">{fallback}</p>
    {failed && <details><summary>查看图示文本</summary><pre>{source}</pre></details>}
  </figure>;
}
