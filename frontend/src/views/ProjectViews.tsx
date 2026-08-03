import type { FormEvent } from 'react'

import type { BossInfo, Project, ScriptModel, Shot } from '../api/types'

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

interface ProjectsViewProps {
  busy: boolean
  projects: Project[]
  onNew: () => void
  onOpen: (project: Project) => void
}

export function ProjectsView({ busy, projects, onNew, onOpen }: ProjectsViewProps) {
  return (
    <section>
      <div className="page-heading">
        <div>
          <p className="eyebrow">PROJECTS</p>
          <h1>把一道招牌菜，讲成一个好故事</h1>
          <p>用引导式问卷快速生成可直接开拍的竖屏脚本。</p>
        </div>
        <button className="primary-button" type="button" onClick={onNew}>＋ 新建项目</button>
      </div>
      {busy && projects.length === 0 ? (
        <div className="empty-state">正在加载项目…</div>
      ) : projects.length === 0 ? (
        <div className="empty-state">
          <span>🎬</span><h2>还没有脚本项目</h2>
          <p>从老板信息和招牌菜开始，几分钟完成第一版脚本。</p>
          <button className="primary-button" type="button" onClick={onNew}>创建第一个项目</button>
        </div>
      ) : (
        <div className="project-grid">
          {projects.map((item) => (
            <button className="project-card" type="button" key={item.id} onClick={() => onOpen(item)}>
              <span className={`status-dot ${item.script ? 'ready' : ''}`} />
              <h2>{item.name}</h2>
              <p>{item.boss_info.cuisine_type ?? '待完善问卷'}</p>
              <footer>
                <span>{item.script ? `${item.script.shots.length} 个镜头` : '尚未生成'}</span>
                <time>{formatDate(item.created_at)}</time>
              </footer>
            </button>
          ))}
        </div>
      )}
    </section>
  )
}

interface SetupViewProps {
  project: Project | null
  script: ScriptModel | null
  form: BossInfo
  dishText: string
  busy: boolean
  onFormChange: (form: BossInfo) => void
  onDishTextChange: (value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function SetupView({
  project, script, form, dishText, busy, onFormChange, onDishTextChange, onSubmit,
}: SetupViewProps) {
  return (
    <section className="setup-layout">
      <aside className="setup-intro">
        <p className="eyebrow">SCRIPT BRIEF</p><h1>先认识你的店</h1>
        <p>这些信息会被直接填进镜头台词。写得越具体，脚本越像老板本人。</p>
        <ol><li className="active">门店与菜品</li><li>老板与顾客</li><li>视频表达</li></ol>
        {project && !script && (
          <div className="empty-hint"><strong>这个项目还没有脚本</strong><span>完成右侧问卷后即可生成第一版，生成结果可以继续手工修改。</span></div>
        )}
      </aside>
      <form className="setup-form" onSubmit={onSubmit}>
        <div className="form-section"><span className="step-number">01</span><div><h2>门店与菜品</h2><p>告诉观众，你是谁、最值得吃什么。</p></div></div>
        <label>店名<input required value={form.restaurant_name} onChange={(e) => onFormChange({ ...form, restaurant_name: e.target.value })} placeholder="例如：阿芳家常菜" /></label>
        <div className="two-columns">
          <label>菜系<select value={form.cuisine_type} onChange={(e) => onFormChange({ ...form, cuisine_type: e.target.value })}><option>家常菜</option><option>川菜</option><option>火锅</option><option>烧烤</option><option>其他</option></select></label>
          <label>招牌菜<input required value={dishText} onChange={(e) => onDishTextChange(e.target.value)} placeholder="多道菜用顿号隔开" /></label>
        </div>
        <div className="form-section divider"><span className="step-number">02</span><div><h2>老板与顾客</h2><p>让口播有人物感，也清楚说给谁听。</p></div></div>
        <label>老板人设 / 口播风格<textarea value={form.owner_persona} onChange={(e) => onFormChange({ ...form, owner_persona: e.target.value })} placeholder="例如：爽快、爱开玩笑，坚持每天亲自选菜" rows={3} /></label>
        <label>目标人群<input value={form.audience} onChange={(e) => onFormChange({ ...form, audience: e.target.value })} placeholder="例如：附近上班族、周末家庭聚餐" /></label>
        <div className="form-section divider"><span className="step-number">03</span><div><h2>视频表达</h2><p>选好平台和节奏，镜头时长会自动适配。</p></div></div>
        <div className="three-columns">
          <label>发布平台<select value={form.platform} onChange={(e) => onFormChange({ ...form, platform: e.target.value })}><option>抖音</option><option>视频号</option><option>小红书</option><option>快手</option></select></label>
          <label>视频风格<select value={form.video_style} onChange={(e) => onFormChange({ ...form, video_style: e.target.value })}><option>竖屏口播</option><option>探店纪实</option><option>后厨展示</option></select></label>
          <label>目标时长（秒）<input type="number" min={15} max={180} value={form.target_duration_seconds} onChange={(e) => onFormChange({ ...form, target_duration_seconds: Number(e.target.value) })} /></label>
        </div>
        <label>开头偏好（可选）<input value={form.hook_preference} onChange={(e) => onFormChange({ ...form, hook_preference: e.target.value })} placeholder="例如：先问一句“你吃过会爆汁的红烧肉吗？”" /></label>
        <div className="form-actions"><span>纯模板生成 · 无模型费用 · 结果可编辑</span><button className="primary-button" type="submit" disabled={busy}>{busy ? '正在生成…' : '生成脚本 →'}</button></div>
      </form>
    </section>
  )
}

interface ScriptViewProps {
  project: Project | null
  script: ScriptModel
  busy: boolean
  onScriptChange: (script: ScriptModel) => void
  onUpdateShot: (index: number, field: keyof Shot, value: string | number) => void
  onSetup: () => void
  onMaterials: () => void
  onSave: () => void
}

export function ScriptView({ project, script, busy, onScriptChange, onUpdateShot, onSetup, onMaterials, onSave }: ScriptViewProps) {
  return (
    <section className="script-page">
      <div className="script-toolbar"><div><p className="eyebrow">SHOOTING SCRIPT</p><h1>{project?.name}</h1><p>{script.shots.length} 个镜头 · 目标 {script.target_duration_seconds} 秒 · {script.style}</p></div>
        <div className="toolbar-actions"><button className="ghost-button" type="button" onClick={onSetup}>修改问卷</button><button className="ghost-button" type="button" onClick={onMaterials}>管理素材</button><button className="primary-button" type="button" disabled={busy} onClick={onSave}>{busy ? '保存中…' : '保存修改'}</button></div>
      </div>
      <div className="script-summary">
        <label>脚本标题<input value={script.title} onChange={(e) => onScriptChange({ ...script, title: e.target.value })} /></label>
        <label>开场钩子<textarea rows={2} value={script.opening_hook} onChange={(e) => onScriptChange({ ...script, opening_hook: e.target.value })} /></label>
        <label>行动引导 CTA<textarea rows={2} value={script.cta} onChange={(e) => onScriptChange({ ...script, cta: e.target.value })} /></label>
      </div>
      {script.shots.length === 0 ? (
        <div className="empty-state compact"><span>📝</span><h2>脚本里还没有镜头</h2><p>返回问卷重新生成，或补充镜头后再保存。</p></div>
      ) : (
        <div className="shot-list">{script.shots.map((shot, index) => (
          <article className="shot-card" key={shot.shot_index}>
            <header><span className="shot-index">{String(shot.shot_index).padStart(2, '0')}</span><div><strong>{shot.location || `镜头 ${shot.shot_index}`}</strong><small>{shot.angle}</small></div><label className="duration-field"><input aria-label={`镜头 ${shot.shot_index} 时长`} type="number" min={0} value={shot.duration_hint_seconds} onChange={(e) => onUpdateShot(index, 'duration_hint_seconds', Number(e.target.value))} />秒</label></header>
            <label>台词<textarea rows={3} value={shot.lines} onChange={(e) => onUpdateShot(index, 'lines', e.target.value)} /></label>
            <label>拍摄要点<textarea rows={3} value={shot.shooting_tips} onChange={(e) => onUpdateShot(index, 'shooting_tips', e.target.value)} /></label>
          </article>
        ))}</div>
      )}
    </section>
  )
}
