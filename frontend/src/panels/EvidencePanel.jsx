export default function EvidencePanel({ evidence }) {
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-100 shrink-0">
        <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider flex items-center gap-2">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>
          </svg>
          Evidence
        </h2>
        {evidence.length > 0 && (
          <span className="text-[11px] font-medium text-neutral-400 bg-neutral-100 px-2 py-0.5 rounded-full">
            {evidence.length} source{evidence.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Evidence items */}
      <div className="flex-1 overflow-y-auto">
        {evidence.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-6">
            <div className="w-14 h-14 rounded-2xl bg-neutral-50 border border-neutral-100 flex items-center justify-center mb-3">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
            </div>
            <h3 className="text-xs font-medium text-neutral-500 mb-1">No evidence yet</h3>
            <p className="text-[11px] text-neutral-400 max-w-[200px]">Retrieved document passages will appear here when you ask a question.</p>
          </div>
        ) : (
          <div className="p-3 space-y-2">
            {evidence.map((item, i) => (
              <div key={i} className="border border-neutral-200 rounded-lg overflow-hidden animate-fade-in">
                {/* Source header */}
                <div className="flex items-center justify-between px-3 py-2 bg-neutral-50 border-b border-neutral-100">
                  <div className="flex items-center gap-2 min-w-0">
                    <svg className="shrink-0 text-neutral-400" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
                    </svg>
                    <span className="text-[12px] font-medium text-neutral-700 truncate">{item.filename}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    <span className="text-[11px] font-semibold text-neutral-500 bg-white border border-neutral-200 px-1.5 py-0.5 rounded">
                      Page {item.page_number}
                    </span>
                  </div>
                </div>

                {/* Content */}
                <div className="px-3 py-2.5">
                  <p className="text-[12px] leading-relaxed text-neutral-700 font-mono whitespace-pre-wrap">
                    {item.content.length > 400 ? item.content.substring(0, 400) + '...' : item.content}
                  </p>
                </div>

                {/* Score */}
                <div className="px-3 py-1.5 border-t border-neutral-100 flex items-center justify-between">
                  <span className="text-[10px] font-medium text-neutral-400 uppercase tracking-wide">Relevance</span>
                  <div className="flex items-center gap-1.5">
                    <div className="w-16 h-1 bg-neutral-200 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-700 ${
                          item.relevance_score >= 0.8 ? 'bg-emerald-500' :
                          item.relevance_score >= 0.65 ? 'bg-amber-500' : 'bg-neutral-400'
                        }`}
                        style={{ width: `${(item.relevance_score * 100)}%` }}
                      />
                    </div>
                    <span className="text-[11px] font-semibold text-neutral-600">
                      {(item.relevance_score * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
