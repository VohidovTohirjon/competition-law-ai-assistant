import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type MdNode = { type: string; value?: string; children?: MdNode[]; data?: Record<string, unknown> }

/** "[2]" in the model's text becomes a small ribboned numeral, the same mark the source card carries. */
function citationMarks() {
  return (tree: MdNode) => {
    const walk = (node: MdNode) => {
      if (!node.children) return
      const next: MdNode[] = []
      for (const child of node.children) {
        if (child.type !== 'text') { walk(child); next.push(child); continue }
        for (const part of String(child.value).split(/(\[\d+\])/)) {
          const found = /^\[(\d+)\]$/.exec(part)
          if (found) {
            next.push({
              type: 'emphasis',
              data: { hName: 'sup', hProperties: { className: 'mk', 'aria-label': `Manba ${found[1]}` } },
              children: [{ type: 'text', value: found[1] }],
            })
          } else if (part) {
            next.push({ type: 'text', value: part })
          }
        }
      }
      node.children = next
    }
    walk(tree)
  }
}

export function AnswerMarkdown({ children }: { children: string }) {
  return (
    <div className="answer markdown-answer">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, citationMarks]}
        components={{
          a: ({ children: label, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer">{label}</a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
