import { useState, useEffect, useCallback } from 'react'
import './App.css'
import DocumentPanel from './panels/DocumentPanel'
import ChatPanel from './panels/ChatPanel'
import EvidencePanel from './panels/EvidencePanel'
import QuestionnairePanel from './panels/QuestionnairePanel'

const API = 'http://localhost:8000'

function App() {
  const [health, setHealth] = useState({ backend: false, ollama: false, chromadb: false })
  const [documents, setDocuments] = useState([])
  const [evidence, setEvidence] = useState([])
  const [chatReady, setChatReady] = useState(false)
  const [activeTab, setActiveTab] = useState('chat')

  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/health`)
      const data = await res.json()
      setHealth(data)
    } catch {
      setHealth({ backend: false, ollama: false, chromadb: false })
    }
  }, [])

  const loadDocuments = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/documents`)
      const data = await res.json()
      setDocuments(data.documents || [])
    } catch { }
  }, [])

  useEffect(() => {
    checkHealth()
    loadDocuments()
    const interval = setInterval(checkHealth, 30000)
    return () => clearInterval(interval)
  }, [checkHealth, loadDocuments])

  useEffect(() => {
    const hasReady = documents.some(
      d => d.status === 'ready' || (d.chunk_count > 0 && d.status !== 'error')
    )
    setChatReady(hasReady)
  }, [documents])

  const handleEvidenceUpdate = useCallback((newEvidence) => {
    setEvidence(newEvidence)
  }, [])

  const allOnline = health.backend && health.ollama && health.chromadb

  return (
    <div className="flex flex-col h-screen bg-[#fafafa]">
      {/* ═══ Header ═══ */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-neutral-200 bg-white shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-neutral-900 flex items-center justify-center">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
            </svg>
          </div>
          <div>
            <h1 className="text-base font-semibold text-neutral-900 leading-tight">ComplianceAI</h1>
            <p className="text-[11px] text-neutral-400 font-medium tracking-wide uppercase">Local Document Intelligence</p>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex items-center gap-1 bg-neutral-100 rounded-lg p-0.5">
          <button
            onClick={() => setActiveTab('chat')}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${activeTab === 'chat' ? 'bg-white text-neutral-900 shadow-sm' : 'text-neutral-500 hover:text-neutral-700'}`}
          >
            <span className="flex items-center gap-1.5">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
              Chat
            </span>
          </button>
          <button
            onClick={() => setActiveTab('questionnaire')}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${activeTab === 'questionnaire' ? 'bg-white text-neutral-900 shadow-sm' : 'text-neutral-500 hover:text-neutral-700'}`}
          >
            <span className="flex items-center gap-1.5">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
              Questionnaire
            </span>
          </button>
        </div>

        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-neutral-200 bg-neutral-50">
          <span className={`w-2 h-2 rounded-full ${allOnline ? 'bg-emerald-500' : 'bg-amber-500'}`} />
          <span className="text-xs font-medium text-neutral-600">
            {allOnline ? 'All Systems Online' : health.backend ? (health.ollama ? 'ChromaDB offline' : 'Ollama offline') : 'Backend offline'}
          </span>
        </div>
      </header>

      {/* ═══ Main Layout ═══ */}
      <div className="flex flex-1 min-h-0">
        {/* Left Panel — Documents */}
        <div className="w-[300px] shrink-0 border-r border-neutral-200 bg-white flex flex-col">
          <DocumentPanel documents={documents} onDocumentsChange={loadDocuments} apiBase={API} />
        </div>

        {/* Center Panel — Chat or Questionnaire */}
        <div className="flex-1 flex flex-col min-w-0 bg-[#fafafa]">
          {activeTab === 'chat' ? (
            <ChatPanel chatReady={chatReady} onEvidenceUpdate={handleEvidenceUpdate} apiBase={API} />
          ) : (
            <QuestionnairePanel chatReady={chatReady} />
          )}
        </div>

        {/* Right Panel — Evidence (only show for chat tab) */}
        {activeTab === 'chat' && (
          <div className="w-[340px] shrink-0 border-l border-neutral-200 bg-white flex flex-col">
            <EvidencePanel evidence={evidence} />
          </div>
        )}
      </div>

      {/* ═══ Footer ═══ */}
      <footer className="flex items-center justify-center gap-4 px-6 py-2 border-t border-neutral-200 bg-white text-[11px] text-neutral-400 shrink-0">
        <span>ComplianceAI v1.0</span>
        <span>·</span>
        <span>Fully Local & Offline</span>
        <span>·</span>
        <span>Powered by Ollama + ChromaDB</span>
      </footer>
    </div>
  )
}

export default App
