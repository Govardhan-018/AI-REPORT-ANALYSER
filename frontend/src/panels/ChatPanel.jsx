import { useState, useRef, useEffect, useCallback } from 'react'

export default function ChatPanel({ chatReady, onEvidenceUpdate, apiBase }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [conversationHistory, setConversationHistory] = useState([])
  const [modelsData, setModelsData] = useState(null)
  const [selectedModel, setSelectedModel] = useState({ provider: 'ollama', model: null })

  useEffect(() => {
    const fetchModels = async () => {
      try {
        const res = await fetch(`${apiBase}/api/models`)
        if (res.ok) {
          const data = await res.json()
          setModelsData(data.providers)
          // Set default if ollama is available
          if (data.providers.ollama?.available && data.providers.ollama.models.length > 0) {
            setSelectedModel({ provider: 'ollama', model: data.providers.ollama.models[0] })
          } else if (data.providers.groq?.available) {
             setSelectedModel({ provider: 'groq', model: data.providers.groq.models[0] })
          }
        }
      } catch (e) {
        console.error("Failed to fetch models", e)
      }
    }
    fetchModels()
  }, [apiBase])
  const messagesEndRef = useRef(null)
  const textareaRef = useRef(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(scrollToBottom, [messages])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const sendMessage = useCallback(async () => {
    const query = input.trim()
    if (!query || !chatReady || isStreaming) return

    // Add user message
    const userMsg = { role: 'user', content: query }
    setMessages(prev => [...prev, userMsg])
    const newHistory = [...conversationHistory, userMsg]
    setConversationHistory(newHistory)
    setInput('')
    setIsStreaming(true)

    // Clear evidence for new query
    onEvidenceUpdate([])

    try {
      const response = await fetch(`${apiBase}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query,
          provider: selectedModel.provider,
          model: selectedModel.model,
          conversation_history: newHistory.slice(-10),
        }),
      })

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: 'Request failed' }))
        throw new Error(err.detail || 'Chat request failed')
      }

      // Parse SSE stream
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullAnswer = ''
      let confidence = 'Low'
      let streamingStarted = false

      // Add placeholder assistant message
      const assistantIdx = messages.length + 1  // +1 for user message we just added
      setMessages(prev => [...prev, { role: 'assistant', content: '', streaming: true }])

      while (true) {
        const { value, done } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        let currentEvent = ''

        for (const line of lines) {
          if (line.startsWith('event:')) {
            currentEvent = line.substring(6).trim()
          } else if (line.startsWith('data:')) {
            const currentData = line.substring(5).trim()
            if (currentEvent && currentData) {
              try {
                const payload = JSON.parse(currentData)

                switch (currentEvent) {
                  case 'evidence':
                    onEvidenceUpdate(payload.evidence || [])
                    break

                  case 'token':
                    fullAnswer += payload.content
                    setMessages(prev => {
                      const updated = [...prev]
                      updated[updated.length - 1] = { role: 'assistant', content: fullAnswer, streaming: true }
                      return updated
                    })
                    break

                  case 'done':
                    confidence = payload.confidence || 'Low'
                    setMessages(prev => {
                      const updated = [...prev]
                      updated[updated.length - 1] = { role: 'assistant', content: fullAnswer, confidence, streaming: false }
                      return updated
                    })
                    break

                  case 'error':
                    setMessages(prev => {
                      const updated = [...prev]
                      updated[updated.length - 1] = { role: 'assistant', content: `⚠️ ${payload.message}`, streaming: false }
                      return updated
                    })
                    break
                }
              } catch { /* skip parse errors */ }
              currentEvent = ''
            }
          }
        }
      }

      if (fullAnswer) {
        setConversationHistory(prev => [...prev, { role: 'assistant', content: fullAnswer }])
      }
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ ${e.message}`, streaming: false }])
    }

    setIsStreaming(false)
  }, [input, chatReady, isStreaming, conversationHistory, apiBase, onEvidenceUpdate, messages.length, selectedModel])

  const clearChat = () => {
    setMessages([])
    setConversationHistory([])
    onEvidenceUpdate([])
  }

  // Auto-resize textarea
  const handleInputChange = (e) => {
    setInput(e.target.value)
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px'
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-200 shrink-0">
        <div className="flex items-center gap-4">
          <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider flex items-center gap-2">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            Ask Questions
          </h2>
          
          {/* Model Selector */}
          {modelsData && (
            <select 
              value={`${selectedModel.provider}:${selectedModel.model || ''}`}
              onChange={(e) => {
                const [provider, ...modelParts] = e.target.value.split(':');
                setSelectedModel({ provider, model: modelParts.join(':') })
              }}
              className="text-xs bg-neutral-50 border border-neutral-200 rounded px-2 py-1 text-neutral-700 outline-none focus:border-neutral-400 max-w-[200px]"
            >
              {modelsData.ollama?.available && modelsData.ollama.models.length > 0 && (
                <optgroup label="Local (Ollama)">
                  {modelsData.ollama.models.map(m => (
                    <option key={`ollama:${m}`} value={`ollama:${m}`}>{m}</option>
                  ))}
                </optgroup>
              )}
              {modelsData.groq?.available && (
                <optgroup label="Cloud (Groq - Fast)">
                  {modelsData.groq.models.map(m => (
                    <option key={`groq:${m}`} value={`groq:${m}`}>{m}</option>
                  ))}
                </optgroup>
              )}
              {modelsData.openrouter?.available && (
                <optgroup label="Cloud (OpenRouter)">
                  {modelsData.openrouter.models.map(m => (
                    <option key={`openrouter:${m}`} value={`openrouter:${m}`}>{m}</option>
                  ))}
                </optgroup>
              )}
            </select>
          )}
        </div>
        {messages.length > 0 && (
          <button onClick={clearChat} className="text-[11px] font-medium text-neutral-400 hover:text-neutral-600 transition-colors flex items-center gap-1">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            Clear
          </button>
        )}
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-5 py-4 min-h-0">
        {!chatReady ? (
          /* Lock overlay */
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-16 h-16 rounded-2xl border-2 border-neutral-200 flex items-center justify-center mb-4">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#a3a3a3" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-neutral-600 mb-1">Upload a document first</h3>
            <p className="text-xs text-neutral-400 max-w-xs">Chat will unlock once a document is processed and indexed.</p>
          </div>
        ) : messages.length === 0 ? (
          /* Welcome state */
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-12 h-12 rounded-xl bg-neutral-100 flex items-center justify-center mb-3">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#525252" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-neutral-700 mb-1">Ready to assist</h3>
            <p className="text-xs text-neutral-400 max-w-sm">Ask questions about your uploaded documents. Every answer is grounded in your data with evidence citations.</p>
          </div>
        ) : (
          /* Messages */
          <div className="space-y-4">
            {messages.map((msg, i) => (
              <div key={i} className={`flex gap-3 animate-fade-in ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                {msg.role === 'assistant' && (
                  <div className="w-7 h-7 rounded-lg bg-neutral-900 flex items-center justify-center shrink-0 mt-0.5">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                  </div>
                )}
                <div className={`max-w-[85%] ${msg.role === 'user' ? 'bg-neutral-900 text-white rounded-2xl rounded-br-md px-4 py-2.5' : 'bg-white border border-neutral-200 rounded-2xl rounded-bl-md px-4 py-3 shadow-sm'}`}>
                  <div className={`text-[13px] leading-relaxed whitespace-pre-wrap ${msg.role === 'user' ? '' : 'text-neutral-800 font-mono'}`}>
                    {msg.content || (msg.streaming ? <span className="flex gap-1"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></span> : '')}
                  </div>
                  {msg.confidence && !msg.streaming && (
                    <div className={`mt-2 pt-2 border-t ${msg.role === 'user' ? 'border-neutral-700' : 'border-neutral-100'}`}>
                      <span className={`inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide ${
                        msg.confidence === 'High' ? 'text-emerald-600' : msg.confidence === 'Medium' ? 'text-amber-600' : 'text-neutral-400'
                      }`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${
                          msg.confidence === 'High' ? 'bg-emerald-500' : msg.confidence === 'Medium' ? 'bg-amber-500' : 'bg-neutral-400'
                        }`} />
                        Confidence: {msg.confidence}
                      </span>
                    </div>
                  )}
                </div>
                {msg.role === 'user' && (
                  <div className="w-7 h-7 rounded-lg bg-neutral-200 flex items-center justify-center shrink-0 mt-0.5">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#525252" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                  </div>
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="px-5 py-3 border-t border-neutral-200 shrink-0">
        <div className="flex items-end gap-2">
          <div className="flex-1 relative">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder={chatReady ? 'Ask a question about your documents...' : 'Upload a document first...'}
              disabled={!chatReady || isStreaming}
              rows={1}
              className="w-full resize-none border border-neutral-300 rounded-xl px-4 py-2.5 text-[13px] text-neutral-800 placeholder:text-neutral-400 focus:outline-none focus:border-neutral-500 focus:ring-1 focus:ring-neutral-200 disabled:opacity-50 disabled:bg-neutral-50 transition-colors"
            />
          </div>
          <button
            onClick={sendMessage}
            disabled={!chatReady || isStreaming || !input.trim()}
            className="shrink-0 w-10 h-10 rounded-xl bg-neutral-900 text-white flex items-center justify-center disabled:opacity-30 hover:bg-neutral-800 transition-colors"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          </button>
        </div>
      </div>
    </div>
  )
}
