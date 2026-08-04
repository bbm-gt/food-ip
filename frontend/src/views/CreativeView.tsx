import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'

import type {
  CreativeBrief,
  CreativeConversation,
  CreativeMode,
  Project,
  TopicCard,
} from '../api/types'

const MODE_OPTIONS: Array<{
  mode: CreativeMode
  icon: string
  title: string
  description: string
}> = [
  {
    mode: 'own_idea',
    icon: '💡',
    title: '我有一个想法',
    description: '把脑子里的点子说出来，AI 帮你补完整。',
  },
  {
    mode: 'ai_recommendation',
    icon: '✨',
    title: '帮我推荐选题',
    description: '结合门店和 IP 定位，一起找这期拍什么。',
  },
  {
    mode: 'revise_script',
    icon: '📝',
    title: '修改已有脚本',
    description: '告诉 AI 哪儿不满意，再整理一个修改方向。',
  },
]

const MODE_NAMES: Record<CreativeMode, string> = {
  own_idea: '我有一个想法',
  ai_recommendation: 'AI 推荐选题',
  revise_script: '修改已有脚本',
}

const STAGE_NAMES = {
  collecting: '还在聊',
  brief_ready: '可以确认',
  confirmed: '已经确认',
}

const DIFFICULTY_NAMES = {
  low: '低',
  medium: '中',
  high: '高',
}

function textValue(value: string) {
  return value.trim() || '待聊天补充'
}

function listValue(values: string[]) {
  const clean = values.filter((value) => value.trim())
  return clean.length ? clean.join('、') : '待聊天补充'
}

function BriefCard({ brief }: { brief: CreativeBrief | null }) {
  const evidence = brief?.evidence ?? []
  return <aside className="brief-card">
    <header>
      <div><span className="eyebrow">CURRENT BRIEF</span><h2>这期视频想怎么拍</h2></div>
      {brief?.confirmed && <span className="confirmed-badge">已确认</span>}
    </header>
    <dl className="brief-fields">
      <div><dt>想法</dt><dd>{textValue(brief?.idea ?? '')}</dd></div>
      <div><dt>视频目标</dt><dd>{textValue(brief?.goal ?? '')}</dd></div>
      <div><dt>目标顾客</dt><dd>{textValue(brief?.target_customer ?? '')}</dd></div>
      <div><dt>核心信息</dt><dd>{textValue(brief?.key_message ?? '')}</dd></div>
      <div><dt>真实证据</dt><dd>{evidence.length ? <ul className="evidence-list">{evidence.map((item, index) => <li key={`${item.statement}-${index}`}><span>{item.statement}</span><small className={item.verified ? 'verified' : ''}>{item.verified ? '档案已核实' : '本期提供'}</small></li>)}</ul> : '待聊天补充'}</dd></div>
      <div><dt>语气风格</dt><dd>{textValue(brief?.tone ?? '')}</dd></div>
      <div><dt>视频形式</dt><dd>{textValue(brief?.format ?? '')}</dd></div>
      <div><dt>拍摄限制</dt><dd>{listValue(brief?.shooting_constraints ?? [])}</dd></div>
      <div><dt>行动引导</dt><dd>{textValue(brief?.cta ?? '')}</dd></div>
    </dl>
  </aside>
}

interface CreativeViewProps {
  project: Project
  conversation: CreativeConversation | null
  busy: boolean
  onStart: (mode: CreativeMode) => Promise<void>
  onSend: (content: string, clientMessageId: string) => Promise<boolean>
  onConfirm: () => Promise<void>
  onGenerateTopics: () => Promise<void>
  onGenerateFromTopic: (topicCard: TopicCard) => Promise<void>
  onGenerate: () => Promise<void>
  onDirectGenerate: () => Promise<void>
  onBack: () => void
}

export function CreativeView({
  project,
  conversation,
  busy,
  onStart,
  onSend,
  onConfirm,
  onGenerateTopics,
  onGenerateFromTopic,
  onGenerate,
  onDirectGenerate,
  onBack,
}: CreativeViewProps) {
  const [draft, setDraft] = useState('')
  const [pendingMessageId, setPendingMessageId] = useState<string | null>(null)

  useEffect(() => {
    setDraft('')
    setPendingMessageId(null)
  }, [conversation?.id])

  async function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const content = draft.trim()
    if (!content || !conversation || busy) return
    const messageId = pendingMessageId ?? `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setPendingMessageId(messageId)
    if (await onSend(content, messageId)) {
      setDraft('')
      setPendingMessageId(null)
    }
  }

  const confirmed = conversation?.stage === 'confirmed'
  const ready = conversation?.stage === 'brief_ready'
  const topicCardSet = conversation?.topic_card_set ?? null

  return <section className="creative-page">
    <div className="script-toolbar">
      <div><p className="eyebrow">SCRIPT CO-CREATION</p><h1>和 AI 聊脚本想法</h1><p>{project.name} · 你只管说人话，AI 负责把想法整理清楚。</p></div>
      <button className="ghost-button" type="button" onClick={onBack}>返回上一步</button>
    </div>

    <section className="creative-entry">
      <div className="creative-entry-heading"><div><h2>{conversation ? '当前对话不合适时，再开一轮' : '这次你想怎么开始？'}</h2><p>{conversation ? '下方对话会保留；重新选择入口会创建一轮新的脚本想法。' : '选择最接近现在情况的一项。'}</p></div>{conversation && <span className="conversation-stage">{MODE_NAMES[conversation.mode]} · {STAGE_NAMES[conversation.stage]}</span>}</div>
      <div className="creative-mode-grid">{MODE_OPTIONS.map((option) => {
        const disabled = busy || (option.mode === 'revise_script' && !project.script)
        return <button className="creative-mode-button" type="button" disabled={disabled} onClick={() => void onStart(option.mode)} key={option.mode}>
          <span>{option.icon}</span><strong>{option.title}</strong><small>{option.mode === 'revise_script' && !project.script ? '当前还没有可修改的脚本' : option.description}</small>
        </button>
      })}</div>
      <div className="legacy-entry"><span>旧入口：不聊天、不确认 Brief</span><button className="text-button" type="button" disabled={busy} onClick={() => void onDirectGenerate()}>{busy ? '正在生成…' : '直接按调研生成三套脚本'}</button></div>
    </section>

    {confirmed && topicCardSet && <section className="topic-card-section">
      <div className="topic-card-heading"><div><span className="eyebrow">TOPIC CARDS</span><h2>先选这期拍什么</h2><p>这些只是轻量方向。选中后，AI 才会继续写三套详细脚本。</p></div><span>{topicCardSet.cards.length} 个方向</span></div>
      {topicCardSet.cards.length === 0 ? <div className="empty-state compact"><strong>还没有可选方向</strong><p>点右侧“重新生成选题卡”，AI 会按已确认的 Brief 再试一次。</p></div> : <div className="topic-card-grid">{topicCardSet.cards.map((card, index) => {
        const selected = topicCardSet.selected_topic_card_id === card.id
        return <article className={`topic-card${selected ? ' selected' : ''}`} key={card.id}>
          <header><span>选题 {index + 1}</span><strong>{card.title}</strong>{selected && <small>已选择</small>}</header>
          <dl>
            <div><dt>开头钩子</dt><dd>{card.hook}</dd></div>
            <div><dt>内容角度</dt><dd>{card.angle}</dd></div>
            <div><dt>目标顾客</dt><dd>{card.target_customer}</dd></div>
            <div><dt>符合 IP 的原因</dt><dd>{card.ip_alignment}</dd></div>
            <div><dt>需要真实证据</dt><dd>{card.evidence_needed.join('、')}</dd></div>
            <div><dt>拍摄难度</dt><dd>{DIFFICULTY_NAMES[card.shoot_difficulty]}</dd></div>
            <div><dt>预计时长</dt><dd>{card.estimated_duration_sec} 秒</dd></div>
            <div><dt>行动引导</dt><dd>{card.cta}</dd></div>
          </dl>
          <button className="primary-button" type="button" disabled={busy} onClick={() => void onGenerateFromTopic(card)}>{busy ? '正在生成详细脚本…' : selected ? '按已选方向重新生成三套脚本' : '选这个方向，生成三套脚本'}</button>
        </article>
      })}</div>}
    </section>}

    {conversation ? <div className="creative-workspace">
      <div className="chat-panel">
        <header><div><h2>和 AI 编导聊一聊</h2><p>一次说一件事就行，不用写专业方案。</p></div><span className={`stage-pill ${conversation.stage}`}>{STAGE_NAMES[conversation.stage]}</span></header>
        <div className="message-list" aria-live="polite">
          {!conversation.messages.length && <div className="chat-welcome"><strong>AI 编导已经准备好了</strong><p>{conversation.mode === 'own_idea' ? '先说说你的点子，哪怕只有一句话也可以。' : conversation.mode === 'ai_recommendation' ? '可以直接说“我不知道拍什么”，AI 会结合你的门店情况来问。' : '说说原脚本哪里不满意、希望怎么改。'}</p></div>}
          {conversation.messages.map((item) => <article className={`chat-message ${item.role}`} key={item.id}>
            <small>{item.role === 'owner' ? '你' : 'AI 编导'}</small>
            <p>{item.content}</p>
            {item.questions.length > 0 && <div className="follow-up-questions"><strong>还想确认：</strong><ol>{item.questions.map((question) => <li key={question}>{question}</li>)}</ol></div>}
          </article>)}
        </div>
        {conversation.last_error && <div className="chat-retry"><span>刚才 AI 没有接上，但你的消息已经保存。</span><strong>保留原文字再点一次“发送”，即可继续。</strong></div>}
        <form className="chat-composer" onSubmit={(event) => void submitMessage(event)}>
          <label htmlFor="creative-message">你想告诉 AI 什么？</label>
          <textarea id="creative-message" rows={4} value={draft} disabled={busy || confirmed} placeholder={confirmed ? '这份想法已经确认，如需调整请开启新一轮。' : '例如：我想拍一道招牌菜，但不想太像广告……'} onChange={(event) => { setDraft(event.target.value); if (pendingMessageId) setPendingMessageId(null) }} />
          <div><small>聊天内容只用于整理本期想法，不会自动修改你的长期档案。</small><button className="primary-button" type="submit" disabled={busy || confirmed || !draft.trim()}>{busy ? 'AI 正在整理…' : pendingMessageId ? '重新发送' : '发送给 AI'}</button></div>
        </form>
      </div>

      <div className="brief-column">
        <BriefCard brief={conversation.brief} />
        <div className="brief-actions">
          {!confirmed && <><button className="primary-button" type="button" disabled={busy || !ready} onClick={() => void onConfirm()}>{busy ? '正在确认并生成选题…' : '确认 Brief，生成选题卡'}</button><small>{ready ? '推荐路径：确认后先看几个轻量选题，再决定详细写哪一个。' : '继续回答 AI 的问题，信息完整后即可确认。'}</small></>}
          {confirmed && <>
            <button className="primary-button" type="button" disabled={busy} onClick={() => void onGenerateTopics()}>{busy ? '正在生成选题卡…' : topicCardSet ? '重新生成选题卡' : '生成 4 个选题方向（推荐）'}</button>
            <small>{topicCardSet ? '在上方选择一个方向，再生成详细脚本。' : '先比较轻量方向，不急着阅读完整脚本。'}</small>
            <button className="text-button direct-brief-button" type="button" disabled={busy} onClick={() => void onGenerate()}>{busy ? '正在生成…' : '备用路径：跳过选题卡，直接按 Brief 生成三套脚本'}</button>
          </>}
        </div>
      </div>
    </div> : <div className="creative-empty"><span>💬</span><strong>选一个入口，就可以开始聊</strong><p>已有调研和 IP 定位会作为背景，你不用从头介绍门店。</p></div>}
  </section>
}
