export type MessageDraft = {
  text: string;
  editing: { messageId: string; originalContent: string; originalTimestamp: string; previousDraft: string } | null;
};

type DraftAction =
  | { type: "change"; text: string }
  | { type: "edit"; messageId: string; text: string; timestamp: string }
  | { type: "cancel" }
  | { type: "submitted" }
  | { type: "restore"; draft: MessageDraft }
  | { type: "reset" };

export const emptyMessageDraft: MessageDraft = { text: "", editing: null };

// Editing stays local until submitted. The original content and timestamp
// guard against overwriting a message changed in another browser window.
export function messageDraftReducer(state: MessageDraft, action: DraftAction): MessageDraft {
  switch (action.type) {
    case "change": return { ...state, text: action.text };
    case "edit": return { text: action.text, editing: {
      messageId: action.messageId, originalContent: action.text, originalTimestamp: action.timestamp,
      previousDraft: state.editing?.previousDraft ?? state.text,
    } };
    case "cancel": return state.editing ? { text: state.editing.previousDraft, editing: null } : state;
    case "submitted": return { text: state.editing?.previousDraft ?? "", editing: null };
    case "restore": return action.draft;
    case "reset": return emptyMessageDraft;
  }
}
