import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { cn } from "@/lib/utils"

export function MessageMarkdown({
  children,
  className,
}: {
  children: string
  className?: string
}) {
  return (
    <div
      className={cn(
        "message-markdown min-w-0 text-[0.9375rem] leading-relaxed",
        className
      )}
    >
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children: linkChildren, ...props }) => (
            <a
              {...props}
              target="_blank"
              rel="noreferrer"
              className="font-medium underline decoration-current/40 underline-offset-3 hover:decoration-current"
            >
              {linkChildren}
            </a>
          ),
          code: ({
            children: codeChildren,
            className: codeClassName,
            ...props
          }) => (
            <code
              {...props}
              className={cn("font-mono text-[0.9em]", codeClassName)}
            >
              {codeChildren}
            </code>
          ),
        }}
      >
        {children}
      </Markdown>
    </div>
  )
}
