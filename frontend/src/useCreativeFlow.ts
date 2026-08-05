import { useState } from 'react'
import type {
  CreativeConversation,
  CreativeMode,
  IPProfile,
  Project,
  ResearchProfile,
  ScriptBundle,
  TopicCard,
} from './api/types'
import {
  addOwnerMessage,
  confirmCreativeBrief,
  confirmIpProfile,
  createCreativeConversation,
  generateFromBrief,
  generateIpProfileDraft,
  generateScriptBundle,
  generateTopicCards,
} from './api/client'

export interface CreativeFlowDeps {
  project: Project | null
  ipProfile: IPProfile | null
  research: ResearchProfile
  setProject: (project: Project | null) => void
  setBundle: (bundle: ScriptBundle | null) => void
  setIpProfile: (profile: IPProfile | null) => void
  setCreativeConversations: (
    updater: (current: CreativeConversation[]) => CreativeConversation[],
  ) => void
  setView: (view: string) => void
  setBusy: (busy: boolean) => void
  setError: (message: string) => void
  setMessage: (message: string) => void
}

export interface CreativeFlowControls {
  currentConversation: CreativeConversation | null
  setCurrentConversation: (conversation: CreativeConversation | null) => void
  upsertConversation: (conversation: CreativeConversation) => void
  regenerateIpProfile: () => Promise<void>
  confirmIpPositioning: () => Promise<void>
  startCreativeConversation: (mode: CreativeMode) => Promise<void>
  sendCreativeMessage: (content: string, clientMessageId: string) => Promise<boolean>
  confirmCreativeBriefAndGenerateTopics: () => Promise<void>
  regenerateTopicCards: () => Promise<void>
  generateBundleFromTopic: (card: TopicCard) => Promise<void>
  generateBundleFromBrief: () => Promise<void>
  directGenerateBundle: () => Promise<void>
  leaveCreative: () => void
}

export function useCreativeFlow(deps: CreativeFlowDeps): CreativeFlowControls {
  const [currentConversation, setCurrentConversation] = useState<CreativeConversation | null>(null)
  const {
    project,
    ipProfile,
    research,
    setProject,
    setBundle,
    setIpProfile,
    setCreativeConversations,
    setView,
    setBusy,
    setError,
    setMessage,
  } = deps

  function upsertConversation(conversation: CreativeConversation) {
    setCurrentConversation(conversation)
    setCreativeConversations((current) => {
      const others = current.filter((item) => item.id !== conversation.id)
      return [conversation, ...others].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    })
  }

  async function regenerateIpProfile() {
    if (!project) return
    setBusy(true); setError(''); setMessage('')
    try {
      const draft = await generateIpProfileDraft(project.id)
      setIpProfile(draft)
      setMessage('已按调研重新生成 IP 定位草稿，请确认。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'IP 定位草稿生成失败') }
    finally { setBusy(false) }
  }

  async function confirmIpPositioning() {
    if (!project) return
    setBusy(true); setError(''); setMessage('')
    try {
      const confirmed = await confirmIpProfile(project.id)
      setIpProfile(confirmed)
      setCurrentConversation(null)
      setMessage('IP 定位已确认，可以开始 AI 共创。')
      setView('creative')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'IP 定位确认失败') }
    finally { setBusy(false) }
  }

  async function startCreativeConversation(mode: CreativeMode) {
    if (!project) return
    setBusy(true); setError(''); setMessage('')
    try {
      if (!ipProfile?.confirmed) throw new Error('请先确认 IP 定位，再开始 AI 共创')
      const conversation = await createCreativeConversation(project.id, mode)
      upsertConversation(conversation)
      setMessage('已开始 AI 共创对话。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '创建共创对话失败') }
    finally { setBusy(false) }
  }

  async function sendCreativeMessage(content: string, clientMessageId: string): Promise<boolean> {
    if (!project || !currentConversation) return false
    setBusy(true); setError('')
    try {
      const conversation = await addOwnerMessage(project.id, currentConversation.id, content, 'episode_only', clientMessageId)
      upsertConversation(conversation)
      return true
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '消息发送失败，请重试')
      return false
    } finally { setBusy(false) }
  }

  async function confirmCreativeBriefAndGenerateTopics() {
    if (!project || !currentConversation) return
    setBusy(true); setError(''); setMessage('')
    try {
      const confirmed = await confirmCreativeBrief(project.id, currentConversation.id)
      upsertConversation(confirmed)
      try {
        const cardSet = await generateTopicCards(project.id, confirmed.id)
        upsertConversation({ ...confirmed, topic_card_set: cardSet })
        setMessage('Brief 已确认，已生成选题卡，请选择一个方向。')
      } catch (reason) {
        setMessage('Brief 已确认。选题卡生成失败，可稍后重新生成。')
        setError(reason instanceof Error ? reason.message : '选题卡生成失败')
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Brief 确认失败') }
    finally { setBusy(false) }
  }

  async function regenerateTopicCards() {
    if (!project || !currentConversation) return
    setBusy(true); setError(''); setMessage('')
    try {
      const cardSet = await generateTopicCards(project.id, currentConversation.id)
      upsertConversation({ ...currentConversation, topic_card_set: cardSet })
      setMessage('已重新生成选题卡。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '选题卡生成失败') }
    finally { setBusy(false) }
  }

  async function generateBundleFromTopic(card: TopicCard) {
    if (!project || !currentConversation) return
    setBusy(true); setError(''); setMessage('')
    try {
      const generated = await generateFromBrief(project.id, currentConversation.id, 3, card.id)
      setBundle(generated)
      setProject({ ...project, script_bundle: generated })
      setCurrentConversation(null)
      setView('candidates')
      setMessage(`已按“${card.title}”生成三套脚本方案。`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '脚本生成失败') }
    finally { setBusy(false) }
  }

  async function generateBundleFromBrief() {
    if (!project || !currentConversation) return
    setBusy(true); setError(''); setMessage('')
    try {
      const generated = await generateFromBrief(project.id, currentConversation.id, 3)
      setBundle(generated)
      setProject({ ...project, script_bundle: generated })
      setCurrentConversation(null)
      setView('candidates')
      setMessage('已按 Brief 生成三套脚本方案。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '脚本生成失败') }
    finally { setBusy(false) }
  }

  async function directGenerateBundle() {
    if (!project) return
    setBusy(true); setError(''); setMessage('')
    try {
      const generated = await generateScriptBundle(project.id, research)
      setBundle(generated)
      setProject({ ...project, research, script_bundle: generated })
      setCurrentConversation(null)
      setView('candidates')
      setMessage(`已由 ${generated.model_name || 'AI'} 直接生成三套方案。`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '脚本生成失败') }
    finally { setBusy(false) }
  }

  function leaveCreative() {
    setCurrentConversation(null)
    setView(project?.script_bundle || project?.script ? 'candidates' : 'setup')
  }

  return {
    currentConversation,
    setCurrentConversation,
    upsertConversation,
    regenerateIpProfile,
    confirmIpPositioning,
    startCreativeConversation,
    sendCreativeMessage,
    confirmCreativeBriefAndGenerateTopics,
    regenerateTopicCards,
    generateBundleFromTopic,
    generateBundleFromBrief,
    directGenerateBundle,
    leaveCreative,
  }
}
