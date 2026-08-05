import type { IPProfile, Project } from '../api/types'

interface IpProfileViewProps {
  project: Project
  profile: IPProfile
  busy: boolean
  onRegenerate: () => void
  onConfirm: () => void
  onBack: () => void
}

function textValue(value: string) {
  return value.trim() || '待补充'
}

function listValue(values: string[]) {
  const clean = values.filter((value) => value.trim())
  return clean.length ? clean.join('、') : '待补充'
}

export function IpProfileView({
  project,
  profile,
  busy,
  onRegenerate,
  onConfirm,
  onBack,
}: IpProfileViewProps) {
  return <section className="creative-page">
    <div className="script-toolbar">
      <div><p className="eyebrow">IP POSITIONING</p><h1>确认你的 IP 定位</h1><p>{project.name} · 这一步定义长期人设；确认后不能直接覆盖，请先仔细看一遍。</p></div>
      <div className="toolbar-actions"><button className="ghost-button" type="button" disabled={busy} onClick={onBack}>返回</button></div>
    </div>
    <aside className="brief-card">
      <header>
        <div><span className="eyebrow">CURRENT IP PROFILE</span><h2>你打算让顾客记住的定位</h2></div>
      </header>
      <dl className="brief-fields">
        <div><dt>人设定位</dt><dd>{textValue(profile.persona_positioning)}</dd></div>
        <div><dt>核心顾客</dt><dd>{textValue(profile.core_audience)}</dd></div>
        <div><dt>核心承诺</dt><dd>{textValue(profile.core_promise)}</dd></div>
        <div><dt>记忆点</dt><dd>{listValue(profile.memory_points)}</dd></div>
        <div><dt>内容支柱</dt><dd>{listValue(profile.content_pillars)}</dd></div>
        <div><dt>长期栏目</dt><dd>{listValue(profile.recurring_series)}</dd></div>
        <div><dt>表达风格</dt><dd>{textValue(profile.speaking_style)}</dd></div>
        <div><dt>证据素材</dt><dd>{listValue(profile.evidence_assets)}</dd></div>
        <div><dt>避开话题</dt><dd>{listValue(profile.avoided_topics)}</dd></div>
        <div><dt>转化路径</dt><dd>{listValue(profile.conversion_path)}</dd></div>
      </dl>
    </aside>
    <div className="form-actions">
      <span>如果草稿与实际情况不符，可以重新生成；确认后再做调整只能从「AI 共创」开始。</span>
      <div className="toolbar-actions">
        <button className="ghost-button" type="button" disabled={busy} onClick={onRegenerate}>{busy ? '生成中…' : '按调研重新生成草稿'}</button>
        <button className="primary-button" type="button" disabled={busy} onClick={onConfirm}>{busy ? '确认中…' : '确认定位，开始 AI 共创'}</button>
      </div>
    </div>
  </section>
}
