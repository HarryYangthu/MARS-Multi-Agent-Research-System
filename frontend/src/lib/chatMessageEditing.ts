import { ChatMessageFailure, type ChatMessageView, type Conversation } from "./api";

export type StoredChatMessage = ChatMessageView & { id?: string; turn_id?: string | null };
export type StoredConversation = Omit<Conversation, "messages"> & { messages: StoredChatMessage[]; active_turn_id?: string | null };
export type PendingMessage = { text: string; after: number; startedAt: string; replaceId?: string; expectedTimestamp?: string };

export function pendingMessageSaved(messages: StoredChatMessage[], pending: PendingMessage): boolean {
  if (pending.replaceId) return messages.some(message => message.role === "user" && message.id === pending.replaceId
    && message.content === pending.text && message.timestamp !== pending.expectedTimestamp);
  return messages.slice(pending.after).some(message => message.role === "user" && message.content === pending.text);
}

export async function replaceChatMessage(convId: string, messageId: string, text: string, expectedContent: string, expectedTimestamp: string): Promise<StoredConversation> {
  const base = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
  const response = await fetch(`${base}/api/chat/conversations/${encodeURIComponent(convId)}/messages/${encodeURIComponent(messageId)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, expected_content: expectedContent, expected_timestamp: expectedTimestamp }),
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    const saved = !!detail && typeof detail === "object" && "message_saved" in detail && detail.message_saved === true;
    const error = detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string" ? detail.error
      : typeof detail === "string" ? detail : "未能确认消息替换，请重新读取对话。";
    throw new ChatMessageFailure(error, saved);
  }
  return response.json() as Promise<StoredConversation>;
}
