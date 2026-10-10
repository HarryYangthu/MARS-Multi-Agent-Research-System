export type MessageDraft = {
  text: string;
  editing: { messageId: string; previousDraft: string } | null;
};

type DraftAction =
  | { type: "change"; text: string }
  | { type: "edit"; messageId: string; text: string }
  | { type: "cancel" }
  | { type: "submitted" }
  | { type: "restore"; draft: MessageDraft }
  | { type: "reset" };

export const emptyMessageDraft: MessageDraft = { text: "", editing: null };

// Editing is a draft operation. Sending always appends through the existing
// conversation API; neither this state nor cancellation rewrites history.
export function messageDraftReducer(state: MessageDraft, action: DraftAction): MessageDraft {
  switch (action.type) {
    case "change": return { ...state, text: action.text };
    case "edit": return { text: action.text, editing: {
      messageId: action.messageId, previousDraft: state.editing?.previousDraft ?? state.text,
    } };
    case "cancel": return state.editing ? { text: state.editing.previousDraft, editing: null } : state;
    case "submitted": return { text: state.editing?.previousDraft ?? "", editing: null };
    case "restore": return action.draft;
    case "reset": return emptyMessageDraft;
  }
}
