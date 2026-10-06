"""Streamlit chat UI: ``policypilot ui`` (or ``streamlit run src/policypilot/ui/app.py``).

* The service (LLM client, DB connections, embedding model, shared index) is built once
  per process with ``st.cache_resource``.
* Every browser session gets its own random session ID, chat history and upload index,
  so users never see each other's history or documents.
* Uploaded PDFs are indexed incrementally (by content hash), never re-embedded.
* Streamlit's XSRF protection stays on (see ``.streamlit/config.toml``).
"""
from __future__ import annotations

import hashlib

import streamlit as st

from policypilot.memory import new_session_id
from policypilot.rag.index import HybridIndex
from policypilot.rag.loaders import pdf_text
from policypilot.service import build_service


@st.cache_resource(show_spinner="Starting PolicyPilot ...")
def get_service():
    return build_service()


def main() -> None:
    st.set_page_config(page_title="PolicyPilot", page_icon=":shield:")
    st.title("PolicyPilot")
    st.caption("Ask about customers, vehicles and claims, or about the policy documents.")
    service = get_service()

    state = st.session_state
    if "session_id" not in state:
        state.session_id = new_session_id()
        state.messages = []
        state.uploads = HybridIndex(service.rag_agent.index.embedder)
        state.upload_hashes = set()

    with st.sidebar:
        st.subheader("Your policy documents")
        files = st.file_uploader("Upload PDFs (only visible to you)", type="pdf", accept_multiple_files=True)
        for f in files or []:
            data = f.getvalue()
            digest = hashlib.sha256(data).hexdigest()
            if digest in state.upload_hashes:
                continue
            try:
                added = state.uploads.add_document(pdf_text(data), f.name)
            except Exception as exc:  # noqa: BLE001 - report any PDF problem to the user
                st.error(f"Could not read {f.name}: {exc}")
                continue
            state.upload_hashes.add(digest)
            st.success(f"Indexed {f.name} ({added} chunks)")
        st.caption(f"Shared documents: {', '.join(service.rag_agent.index.sources) or 'none'}")
        if st.button("New conversation"):
            service.sessions.clear(state.session_id)
            state.session_id = new_session_id()
            state.messages = []

    for msg in state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    question = st.chat_input("Ask a question")
    if not question:
        return
    state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Thinking ..."):
            response = service.ask(question, state.session_id, extra_indexes=[state.uploads])
        answer = response.answer
        st.markdown(answer.text)
        st.caption(f"route: {response.decision.route} ({response.decision.source}, "
                   f"confidence {response.decision.confidence:.2f})")
        if answer.query:
            with st.expander("Query that was run"):
                st.code(answer.query)
        if answer.result is not None and answer.result.rows:
            st.dataframe(answer.result.records())
        for s in answer.sources:
            with st.expander(f"[{s.ref}] {s.source}"):
                st.write(s.text)
    state.messages.append({"role": "assistant", "content": answer.text})


main()
