import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';

interface MarkdownContentProps {
  content: string;
  className?: string;
  prose?: boolean;
}

export function MarkdownContent({
  content,
  className = '',
  prose = true,
}: MarkdownContentProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      className={`${prose ? 'prose dark:prose-invert max-w-none prose-pre:text-sm prose-code:text-sm' : 'whitespace-pre-wrap'} ${className}`}
      components={
        {
          table: ({ ...props }) => (
            <div className="overflow-x-auto">
              <table {...props} />
            </div>
          ),
          code: ({ ...props }) => {
            const isInline = !props.className?.includes('language-');
            return isInline ? (
              <code className="whitespace-nowrap" {...props} />
            ) : (
              <code className="whitespace-pre-wrap" {...props} />
            );
          },
        } as Components
      }
    >
      {content}
    </ReactMarkdown>
  );
}
