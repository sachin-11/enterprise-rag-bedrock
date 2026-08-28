import type { ReactNode } from "react";
import { ConversationSidebar } from "./ConversationSidebar";

export default function ChatLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1">
      <ConversationSidebar />
      {children}
    </div>
  );
}
