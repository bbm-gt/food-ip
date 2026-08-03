import { useState } from 'react'
import type { FormEvent } from 'react'

import type {
  Project,
  ResearchProfile,
  ScriptBundle,
  ScriptCandidate,
  ScriptModel,
  Shot,
} from '../api/types'

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

function splitList(value: string): string[] {
  return value.split(/[\n、,，;；/\\]+/).map((item) => item.trim()).filter(Boolean)
}

function joinList(values: string[]): string {
  return values.join('、')
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
          <h1>把一家店，经营成一个有人记住的 IP</h1>
          <p>先建立门店和老板档案，再生成三套定位不同、可以直接开拍的方案。</p>
        </div>
        <button className="primary-button" type="button" onClick={onNew}>＋ 新建项目</button>
      </div>
      {busy && projects.length === 0 ? (
        <div className="empty-state">正在加载项目…</div>
      ) : projects.length === 0 ? (
        <div className="empty-state">
          <span>🎬</span><h2>还没有 IP 项目</h2>
          <p>从深度调研开始，找到老板真正适合长期拍摄的内容方向。</p>
          <button className="primary-button" type="button" onClick={onNew}>创建第一个项目</button>
        </div>
      ) : (
        <div className="project-grid">
          {projects.map((item) => (
            <button className="project-card" type="button" key={item.id} onClick={() => onOpen(item)}>
              <span className={`status-dot ${item.script ? 'ready' : ''}`} />
              <h2>{item.name}</h2>
              <p>{item.research?.store.cuisine_type ?? item.boss_info.cuisine_type ?? '待完善调研'}</p>
              <footer>
                <span>{item.script ? `${item.script.shots.length} 个镜头` : item.script_bundle ? '待选择脚本' : '待完成调研'}</span>
                <time>{formatDate(item.created_at)}</time>
              </footer>
            </button>
          ))}
        </div>
      )}
    </section>
  )
}

const STEPS = [
  ['门店档案', '门店、菜品与经营基础'],
  ['老板本人', '经历、性格与表达边界'],
  ['真实证据', '差异、食材与制作过程'],
  ['顾客目标', '拍给谁看、解决什么问题'],
  ['拍摄条件', '保证生成的镜头真正能拍'],
] as const

interface SetupViewProps {
  project: Project | null
  script: ScriptModel | null
  research: ResearchProfile
  busy: boolean
  onResearchChange: (research: ResearchProfile) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function SetupView({
  project, script, research, busy, onResearchChange, onSubmit,
}: SetupViewProps) {
  const [step, setStep] = useState(0)
  const store = research.store
  const owner = research.owner
  const audience = research.audience
  const shooting = research.shooting
  const updateStore = (patch: Partial<typeof store>) => onResearchChange({ ...research, store: { ...store, ...patch } })
  const updateOwner = (patch: Partial<typeof owner>) => onResearchChange({ ...research, owner: { ...owner, ...patch } })
  const updateAudience = (patch: Partial<typeof audience>) => onResearchChange({ ...research, audience: { ...audience, ...patch } })
  const updateShooting = (patch: Partial<typeof shooting>) => onResearchChange({ ...research, shooting: { ...shooting, ...patch } })
  const coreReady = Boolean(store.restaurant_name.trim() && store.signature_dishes.length)

  return (
    <section className="setup-layout research-layout">
      <aside className="setup-intro">
        <p className="eyebrow">IP RESEARCH</p><h1>先把真实的你找出来</h1>
        <p>脚本不是凭空编故事。信息越具体，生成的三套方案越有差异，也越适合长期运营。</p>
        <ol>{STEPS.map(([title], index) => <li className={step === index ? 'active' : step > index ? 'done' : ''} key={title}><button type="button" onClick={() => setStep(index)}>{index + 1}. {title}</button></li>)}</ol>
        {project && script && <div className="empty-hint"><strong>修改后会生成新一批方案</strong><span>已经选择的脚本不会在填写过程中被覆盖。</span></div>}
      </aside>
      <form className="setup-form research-form" onSubmit={onSubmit}>
        <div className="research-progress"><span>第 {step + 1} / {STEPS.length} 步</span><div><i style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} /></div></div>
        <div className="form-section"><span className="step-number">0{step + 1}</span><div><h2>{STEPS[step][0]}</h2><p>{STEPS[step][1]}</p></div></div>

        {step === 0 && <div className="form-fields">
          <label>店名（必填）<input required value={store.restaurant_name} onChange={(event) => updateStore({ restaurant_name: event.target.value })} placeholder="例如：赵姐炭火小馆" /></label>
          <div className="three-columns">
            <label>所在城市<input value={store.city} onChange={(event) => updateStore({ city: event.target.value })} placeholder="青岛" /></label>
            <label>商圈 / 街道<input value={store.business_district} onChange={(event) => updateStore({ business_district: event.target.value })} placeholder="软件园附近" /></label>
            <label>菜系<select value={store.cuisine_type} onChange={(event) => updateStore({ cuisine_type: event.target.value })}><option>家常菜</option><option>川菜</option><option>火锅</option><option>烧烤</option><option>面食</option><option>饮品甜品</option><option>其他</option></select></label>
          </div>
          <label>招牌菜（必填，多项可用顿号、斜线或回车隔开）<input required value={joinList(store.signature_dishes)} onChange={(event) => updateStore({ signature_dishes: splitList(event.target.value) })} placeholder="炭烤羊肉串、蒜香鸡翅、烤茄子" /></label>
          <div className="three-columns">
            <label>经营年限<input type="number" min={0} value={store.years_in_business} onChange={(event) => updateStore({ years_in_business: Number(event.target.value) })} /></label>
            <label>人均消费<input type="number" min={0} value={store.price_per_person} onChange={(event) => updateStore({ price_per_person: Number(event.target.value) })} /></label>
            <label>经营方式<input value={joinList(store.business_modes)} onChange={(event) => updateStore({ business_modes: splitList(event.target.value) })} placeholder="堂食、外卖、团购" /></label>
          </div>
        </div>}

        {step === 1 && <div className="form-fields">
          <div className="two-columns"><label>老板称呼<input value={owner.owner_name} onChange={(event) => updateOwner({ owner_name: event.target.value })} placeholder="赵姐" /></label><label>家乡<input value={owner.hometown} onChange={(event) => updateOwner({ hometown: event.target.value })} placeholder="黑龙江" /></label></div>
          <label>老板真实性格 / 人设<textarea rows={3} value={owner.owner_persona} onChange={(event) => updateOwner({ owner_persona: event.target.value })} placeholder="爽快、爱开玩笑，但对食材很较真" /></label>
          <label>为什么进入餐饮行业<textarea rows={3} value={owner.origin_story} onChange={(event) => updateOwner({ origin_story: event.target.value })} placeholder="写具体原因，不需要写成文案" /></label>
          <div className="two-columns"><label>经营中最困难的一段经历<textarea rows={3} value={owner.hardest_moment} onChange={(event) => updateOwner({ hardest_moment: event.target.value })} placeholder="只填写愿意公开、并且与开店经营有关的经历" /></label><label>经营中最自豪的一件事<textarea rows={3} value={owner.proudest_moment} onChange={(event) => updateOwner({ proudest_moment: event.target.value })} placeholder="例如老顾客搬家后仍会专程回来" /></label></div>
          <ToggleField label="我允许 AI 将上述个人经历写入公开视频脚本" checked={owner.allow_personal_story} onChange={(checked) => updateOwner({ allow_personal_story: checked })} />
          <div className="three-columns"><label>出镜方式<select value={owner.appearance_mode} onChange={(event) => updateOwner({ appearance_mode: event.target.value as typeof owner.appearance_mode })}><option>真人口播</option><option>旁白</option><option>只拍手部</option><option>不出镜</option></select></label><label>表达风格<select value={owner.speaking_style} onChange={(event) => updateOwner({ speaking_style: event.target.value })}><option>实在真诚</option><option>亲切爱唠嗑</option><option>专业内行</option><option>幽默自黑</option><option>有故事感</option></select></label><label>语言<input value={owner.language_style} onChange={(event) => updateOwner({ language_style: event.target.value })} placeholder="普通话 / 东北话" /></label></div>
          <label>不希望视频涉及的话题<input value={joinList(owner.avoided_topics)} onChange={(event) => updateOwner({ avoided_topics: splitList(event.target.value) })} placeholder="家庭隐私、营业额、配方细节" /></label>
        </div>}

        {step === 2 && <div className="form-fields">
          <label>与同行最不一样的地方<textarea rows={3} value={joinList(store.differentiators)} onChange={(event) => updateStore({ differentiators: splitList(event.target.value) })} placeholder="当天现切现穿，卖完为止、老板亲自选肉" /></label>
          <label>能被拍出来的食材证明<textarea rows={3} value={joinList(store.ingredient_proofs)} onChange={(event) => updateStore({ ingredient_proofs: splitList(event.target.value) })} placeholder="采购单、当天鲜肉、活鱼、现熬汤底" /></label>
          <label>能被拍出来的制作过程<textarea rows={3} value={joinList(store.visible_processes)} onChange={(event) => updateStore({ visible_processes: splitList(event.target.value) })} placeholder="切肉、穿串、炭火烤制" /></label>
          <div className="two-columns"><label>顾客最常夸什么<textarea rows={3} value={joinList(store.customer_praises)} onChange={(event) => updateStore({ customer_praises: splitList(event.target.value) })} /></label><label>顾客最容易误解什么<textarea rows={3} value={joinList(store.customer_misunderstandings)} onChange={(event) => updateStore({ customer_misunderstandings: splitList(event.target.value) })} /></label></div>
        </div>}

        {step === 3 && <div className="form-fields">
          <label>核心顾客是谁<input value={audience.core_audience} onChange={(event) => updateAudience({ core_audience: event.target.value })} placeholder="附近上班族和喜欢夜宵的年轻人" /></label>
          <div className="two-columns"><label>主要用餐场景<textarea rows={3} value={joinList(audience.dining_scenarios)} onChange={(event) => updateAudience({ dining_scenarios: splitList(event.target.value) })} placeholder="工作日晚餐、朋友夜宵" /></label><label>顾客最想得到什么<textarea rows={3} value={joinList(audience.customer_needs)} onChange={(event) => updateAudience({ customer_needs: splitList(event.target.value) })} placeholder="新鲜、分量足、上菜快" /></label></div>
          <label>顾客下单前最担心什么<textarea rows={3} value={joinList(audience.customer_concerns)} onChange={(event) => updateAudience({ customer_concerns: splitList(event.target.value) })} placeholder="羊肉是否新鲜、价格值不值、后厨是否干净" /></label>
          <div className="two-columns"><label>当前最想解决的经营问题<textarea rows={3} value={audience.current_business_problem} onChange={(event) => updateAudience({ current_business_problem: event.target.value })} placeholder="新店没人知道，工作日晚上客流少" /></label><label>内容首要目标<select value={audience.content_goal} onChange={(event) => updateAudience({ content_goal: event.target.value as typeof audience.content_goal })}><option>吸引到店</option><option>团购转化</option><option>账号涨粉</option><option>建立信任</option><option>品牌认知</option></select></label></div>
        </div>}

        {step === 4 && <div className="form-fields">
          <div className="three-columns"><label>发布平台<select value={shooting.platform} onChange={(event) => updateShooting({ platform: event.target.value })}><option>抖音</option><option>视频号</option><option>小红书</option><option>快手</option></select></label><label>内容质感<select value={shooting.video_style} onChange={(event) => updateShooting({ video_style: event.target.value })}><option>烟火气纪实</option><option>竖屏口播</option><option>后厨展示</option><option>温暖故事</option><option>专业干货</option></select></label><label>目标时长（秒）<input type="number" min={15} max={180} value={shooting.target_duration_seconds} onChange={(event) => updateShooting({ target_duration_seconds: Number(event.target.value) })} /></label></div>
          <div className="two-columns"><label>可以拍摄的区域<input value={joinList(shooting.available_locations)} onChange={(event) => updateShooting({ available_locations: splitList(event.target.value) })} placeholder="店门口、后厨、出餐口" /></label><label>不能拍摄的区域<input value={joinList(shooting.unavailable_locations)} onChange={(event) => updateShooting({ unavailable_locations: splitList(event.target.value) })} /></label></div>
          <div className="toggle-grid"><ToggleField label="后厨允许拍摄" checked={shooting.can_show_kitchen} onChange={(checked) => updateShooting({ can_show_kitchen: checked })} /><ToggleField label="顾客可以出镜" checked={shooting.can_show_customers} onChange={(checked) => updateShooting({ can_show_customers: checked })} /></div>
          <div className="three-columns"><label>现有设备<input value={joinList(shooting.equipment)} onChange={(event) => updateShooting({ equipment: splitList(event.target.value) })} placeholder="手机、三脚架、麦克风" /></label><label>每天可拍摄分钟数<input type="number" min={0} value={shooting.daily_minutes} onChange={(event) => updateShooting({ daily_minutes: Number(event.target.value) })} /></label><label>计划更新频率<input value={shooting.update_frequency} onChange={(event) => updateShooting({ update_frequency: event.target.value })} /></label></div>
          <label>开场偏好（选填）<input value={shooting.hook_preference} onChange={(event) => updateShooting({ hook_preference: event.target.value })} placeholder="只卖当天现穿的串，一口能吃出差别吗？" /></label>
        </div>}

        <div className="form-actions research-actions"><span>规则约束 + DeepSeek 生成 + 程序质检</span><div>{step > 0 && <button className="ghost-button" type="button" onClick={() => setStep(step - 1)}>上一步</button>}{step < STEPS.length - 1 ? <button className="primary-button" type="button" disabled={step === 0 && !coreReady} onClick={() => setStep(step + 1)}>下一步</button> : <button className="primary-button" type="submit" disabled={busy || !coreReady}>{busy ? 'AI 正在编排脚本…' : 'AI 生成三套脚本 →'}</button>}</div></div>
      </form>
    </section>
  )
}

function ToggleField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="toggle-field"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span><i />{label}</span></label>
}

interface CandidatesViewProps {
  project: Project
  bundle: ScriptBundle
  busy: boolean
  onSelect: (candidate: ScriptCandidate) => void
  onRegenerate: () => void
  onSetup: () => void
}

export function CandidatesView({ project, bundle, busy, onSelect, onRegenerate, onSetup }: CandidatesViewProps) {
  return <section className="candidate-page">
    <div className="script-toolbar"><div><p className="eyebrow">SCRIPT OPTIONS · {bundle.model_name || '规则生成'}</p><h1>三种方向，选择最像你的一套</h1><p>{bundle.research_summary}</p></div><div className="toolbar-actions"><button className="ghost-button" type="button" onClick={onSetup}>修改调研</button><button className="ghost-button" type="button" disabled={busy} onClick={onRegenerate}>AI 换一批</button></div></div>
    {bundle.warnings.map((warning) => <div className="notice error" key={warning}>{warning}</div>)}
    <div className="candidate-grid">{bundle.candidates.map((candidate, index) => <article className="candidate-card" key={candidate.id}>
      <header><span className="candidate-letter">{String.fromCharCode(65 + index)}</span><div><p>{candidate.strategy_name}</p><h2>{candidate.script.title}</h2></div><strong className="fit-score">{candidate.score}<small>适配分</small></strong></header>
      <p className="candidate-positioning">{candidate.positioning}</p>
      <div className="candidate-meta"><span>拍摄难度：{candidate.difficulty}</span><span>{candidate.requires_owner ? '需要老板参与' : '可弱化口播'}</span></div>
      <div className="candidate-hook"><small>开场钩子</small><strong>{candidate.script.opening_hook}</strong></div>
      <ul className="reason-list">{candidate.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      <details><summary>查看 6 个镜头</summary><ol className="candidate-shots">{candidate.script.shots.map((shot) => <li key={shot.shot_index}><span>{shot.shot_index}</span><div><strong>{shot.location} · {shot.angle}</strong><p>{shot.purpose && `目的：${shot.purpose}｜`}{shot.lines}</p></div></li>)}</ol></details>
      <button className="primary-button candidate-select" type="button" disabled={busy} onClick={() => onSelect(candidate)}>选择方案 {String.fromCharCode(65 + index)} 开始拍摄</button>
    </article>)}</div>
    <p className="candidate-note">适配分来自问卷完整度、真实证据、拍摄条件和经营目标，只代表发布前建议；上线后的观看、关注和到店数据才决定长期权重。</p>
  </section>
}

interface ScriptViewProps {
  project: Project | null
  script: ScriptModel
  busy: boolean
  hasAlternatives: boolean
  onScriptChange: (script: ScriptModel) => void
  onUpdateShot: (index: number, field: keyof Shot, value: string | number) => void
  onSetup: () => void
  onCandidates: () => void
  onMaterials: () => void
  onSave: () => void
}

export function ScriptView({ project, script, busy, hasAlternatives, onScriptChange, onUpdateShot, onSetup, onCandidates, onMaterials, onSave }: ScriptViewProps) {
  return (
    <section className="script-page">
      <div className="script-toolbar"><div><p className="eyebrow">SHOOTING SCRIPT</p><h1>{project?.name}</h1><p>{script.shots.length} 个镜头 · 目标 {script.target_duration_seconds} 秒 · {script.style}</p></div>
        <div className="toolbar-actions"><button className="ghost-button" type="button" onClick={onSetup}>修改调研</button>{hasAlternatives && <button className="ghost-button" type="button" onClick={onCandidates}>其他方案</button>}<button className="ghost-button" type="button" onClick={onMaterials}>管理素材</button><button className="primary-button" type="button" disabled={busy} onClick={onSave}>{busy ? '保存中…' : '保存修改'}</button></div>
      </div>
      <div className="script-summary">
        <label>脚本标题<input value={script.title} onChange={(event) => onScriptChange({ ...script, title: event.target.value })} /></label>
        <label>开场钩子<textarea rows={2} value={script.opening_hook} onChange={(event) => onScriptChange({ ...script, opening_hook: event.target.value })} /></label>
        <label>行动引导 CTA<textarea rows={2} value={script.cta} onChange={(event) => onScriptChange({ ...script, cta: event.target.value })} /></label>
      </div>
      <div className="shot-list">{script.shots.map((shot, index) => (
        <article className="shot-card" key={shot.shot_index}>
          <header><span className="shot-index">{String(shot.shot_index).padStart(2, '0')}</span><div><strong>{shot.location || `镜头 ${shot.shot_index}`}</strong><small>{shot.angle}</small></div><label className="duration-field"><input aria-label={`镜头 ${shot.shot_index} 时长`} type="number" min={0} value={shot.duration_hint_seconds} onChange={(event) => onUpdateShot(index, 'duration_hint_seconds', Number(event.target.value))} />秒</label></header>
          <label>台词<textarea rows={3} value={shot.lines} onChange={(event) => onUpdateShot(index, 'lines', event.target.value)} /></label>
          <label>拍摄要点<textarea rows={3} value={shot.shooting_tips} onChange={(event) => onUpdateShot(index, 'shooting_tips', event.target.value)} /></label>
          <DetailedShotGuide shot={shot} />
        </article>
      ))}</div>
    </section>
  )
}

function DetailList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return <div><strong>{title}</strong><ol>{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ol></div>
}

function DetailedShotGuide({ shot }: { shot: Shot }) {
  const hasDetails = Boolean(shot.purpose || shot.phone_setup || shot.action_steps.length)
  if (!hasDetails) return null
  return <details className="shooting-guide">
    <summary>不会拍？展开详细拍摄教程</summary>
    <div className="shooting-guide-grid">
      <div><strong>镜头目的</strong><p>{shot.purpose}</p></div>
      <div><strong>拍摄主体</strong><p>{shot.subject}</p></div>
      <div className="guide-wide"><strong>手机与机位</strong><p>{shot.phone_setup}</p></div>
      <div><strong>运镜</strong><p>{shot.camera_movement}</p></div>
      <div><strong>声音</strong><p>{shot.audio}</p></div>
      <div><strong>光线</strong><p>{shot.lighting}</p></div>
      <div><strong>所需道具</strong><p>{shot.props.join('、') || '无需额外道具'}</p></div>
      <DetailList title="照着做" items={shot.action_steps} />
      <div><strong>字幕</strong><p>{shot.subtitle || '使用台词自动生成字幕'}</p></div>
      <div><strong>剪辑提示</strong><p>{shot.edit_note}</p></div>
      <DetailList title="常见错误" items={shot.common_mistakes} />
      <DetailList title="出现这些情况要重拍" items={shot.retake_if} />
    </div>
  </details>
}
