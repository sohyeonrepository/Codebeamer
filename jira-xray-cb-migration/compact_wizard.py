"""Compact Jira → Codebeamer UI aligned with the four approved screen sketches.

The existing connector/authentication window is reused by import_wizard.py.
All writes remain behind the original ImportWizard.execute approval gate.
"""
import tkinter as tk
from tkinter import ttk, messagebox
from migration.wizard import field_identifier, semantic_type, hidden
from mapping_helper import source_choices


def launch(wizard, projects):
    root = tk.Tk()
    root.title("Jira → Codebeamer 마이그레이션")
    root.geometry("1150x790")
    status = tk.StringVar(value="서버 연결 완료. 프로젝트와 Tracker를 연결하세요.")
    tabs = ttk.Notebook(root)
    tabs.pack(fill="both", expand=True, padx=14, pady=12)
    project_tab = ttk.Frame(tabs, padding=16)
    auto_tab = ttk.Frame(tabs, padding=16)
    manual_tab = ttk.Frame(tabs, padding=16)
    tabs.add(project_tab, text="1. 프로젝트 연결")
    tabs.add(auto_tab, text="2. 데이터 가져오기 · 추천")
    tabs.add(manual_tab, text="3. 데이터 가져오기 · 수동")
    ttk.Label(root, textvariable=status, wraplength=1080).pack(fill="x", padx=16, pady=(0, 12))

    def guarded(label, fn):
        try:
            result = fn()
            status.set(label + " 완료")
            return result
        except Exception as exc:
            message = str(exc)
            if len(message) > 600:
                message = message[:600] + "… (상세 진단 확인)"
            status.set(label + " 실패: " + message)
            messagebox.showerror(label + " 실패", message)
            return None

    # Page 2 of the sketches: project first, followed by source type and target tracker.
    ttk.Label(project_tab, text="프로젝트 연결", font=("", 19, "bold")).pack(anchor="w", pady=(0, 15))
    jira_projects = projects["jira"]
    cb_projects = projects["codebeamer"]
    jira_names = [str(p["key"]) + " · " + str(p.get("name", "")) for p in jira_projects]
    cb_names = [str(p["id"]) + " · " + str(p.get("name", "")) for p in cb_projects]
    selectors = ttk.Frame(project_tab)
    selectors.pack(fill="x", pady=5)
    ttk.Label(selectors, text="Jira 프로젝트").grid(row=0, column=0, sticky="w")
    ttk.Label(selectors, text="Codebeamer 기존 프로젝트").grid(row=0, column=1, sticky="w", padx=12)
    jira_box = ttk.Combobox(selectors, state="readonly", values=jira_names, width=43)
    cb_box = ttk.Combobox(selectors, state="readonly", values=cb_names, width=43)
    jira_box.grid(row=1, column=0, sticky="w")
    cb_box.grid(row=1, column=1, sticky="w", padx=12)
    types_box = ttk.Combobox(project_tab, state="readonly", width=74)
    trackers_box = ttk.Combobox(project_tab, state="readonly", width=74)
    project_key = [None]
    issue_types = []
    cb_trackers = []
    active_scope = [None]

    def select_project():
        if jira_box.current() < 0 or cb_box.current() < 0:
            raise ValueError("Jira 및 Codebeamer 프로젝트를 선택하세요.")
        key = jira_projects[jira_box.current()]["key"]
        target = cb_projects[cb_box.current()]["id"]
        wizard.select_project(key, target)
        project_key[0] = key
        issue_types[:] = wizard.plan["projects"][key]["types"]
        cb_trackers[:] = wizard.plan["projects"][key]["trackers"]
        types_box.configure(values=[str(t["id"]) + " · " + t["name"] for t in issue_types])
        trackers_box.configure(values=[str(t["id"]) + " · " + t.get("name", "") for t in cb_trackers])
        types_box.set("")
        trackers_box.set("")
        active_scope[0] = None
        status.set("프로젝트 조회 완료. Jira TYPE과 Codebeamer Tracker를 연결하세요.")

    ttk.Button(selectors, text="프로젝트 조회", command=lambda: guarded("프로젝트 조회", select_project)).grid(row=1, column=2, padx=10)
    ttk.Separator(project_tab).pack(fill="x", pady=26)
    ttk.Label(project_tab, text="Jira TYPE (Issue Type)").pack(anchor="w")
    types_box.pack(anchor="w", pady=(4, 14))
    ttk.Label(project_tab, text="CB TRACKER").pack(anchor="w")
    trackers_box.pack(anchor="w", pady=4)
    mapping_notice = tk.StringVar(value="기존 Tracker에 연결합니다. 신규 Tracker 생성은 고급 Wizard에서 지원합니다.")
    ttk.Label(project_tab, textvariable=mapping_notice, wraplength=1000).pack(anchor="w", pady=15)
    scope_names = []
    scope_box = ttk.Combobox(project_tab, state="readonly", width=76)
    ttk.Label(project_tab, text="등록된 Issue Type / Tracker 연결").pack(anchor="w", pady=(15, 3))
    scope_box.pack(anchor="w")

    def save_tracker():
        if not project_key[0] or types_box.current() < 0 or trackers_box.current() < 0:
            raise ValueError("프로젝트와 Jira TYPE, CB Tracker를 선택하세요.")
        kind = issue_types[types_box.current()]
        tracker = cb_trackers[trackers_box.current()]
        wizard.map_tracker(project_key[0], kind["id"], tracker["id"])
        scope = project_key[0] + ":" + str(kind["id"])
        active_scope[0] = scope
        if scope not in scope_names:
            scope_names.append(scope)
        scope_box.configure(values=scope_names)
        scope_box.set(scope)
        status.set(kind["name"] + " → " + tracker.get("name", str(tracker["id"])) + " 연결 완료")

    def current_scope():
        scope = scope_box.get() or active_scope[0]
        if scope not in wizard.plan["scopes"]:
            raise ValueError("먼저 Jira TYPE과 Tracker를 연결하세요.")
        active_scope[0] = scope
        return scope

    ttk.Button(project_tab, text="연결 저장", command=lambda: guarded("Tracker 연결", save_tracker)).pack(anchor="w", pady=12)

    # Pages 3 and 4: both use the same analyzed scope, but provide independent
    # recommendation/review and manual mapping/creation actions.
    ttk.Label(auto_tab, text="데이터 가져오기 · 필드 추천", font=("", 19, "bold")).pack(anchor="w")
    ttk.Label(auto_tab, text="사용 중인 Jira 필드를 조회하고 자동 추천합니다. 선택하지 않은 필드는 가져오지 않습니다.").pack(anchor="w", pady=8)
    ttk.Label(manual_tab, text="데이터 가져오기 · 필드 선택/생성", font=("", 19, "bold")).pack(anchor="w")
    ttk.Label(manual_tab, text="동일명 추천 외의 항목은 무시, 기존 필드 연결 또는 새 필드 생성 요청을 선택합니다.").pack(anchor="w", pady=8)
    cols = ("jira", "cb", "type", "decision")
    tables = []
    for parent in (auto_tab, manual_tab):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=9)
        table = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for key, title, width in (("jira", "Jira 필드", 280), ("cb", "Codebeamer 필드", 330),
                                  ("type", "타입", 130), ("decision", "선택 상태", 190)):
            table.heading(key, text=title)
            table.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        tables.append(table)

    def rows():
        return wizard.plan["scopes"][current_scope()]["rows"]

    def refresh():
        scope = current_scope()
        entry = wizard.plan["scopes"][scope]
        for table in tables:
            table.delete(*table.get_children())
            for source, row in entry.get("rows", {}).items():
                info = entry["analysis"]["fields"].get(source, {})
                target = row.get("target") or {}
                selected = row.get("selected", False)
                decision = ("이관 · " if selected else "제외 · ") + str(row.get("status", "미매핑"))
                if row.get("creation"):
                    target_name = row["creation"]["label"] + " (신규 생성 예정)"
                else:
                    target_name = target.get("label") or target.get("name") or "연결 안 됨"
                kind = semantic_type(info, True)[0] or "미확인"
                table.insert("", "end", iid=source, values=(
                    info.get("name", source), target_name, kind, decision))
        status.set("필드 목록 갱신 완료 · " + str(len(rows())) + "개")

    def analyze():
        scope = current_scope()
        wizard.analyze(scope)
        refresh()
        tabs.select(auto_tab)

    ttk.Button(project_tab, text="완료 · 필드 데이터 가져오기", command=lambda: guarded("필드 분석", analyze)).pack(anchor="w", pady=8)
    ttk.Button(auto_tab, text="필드 다시 가져오기", command=lambda: guarded("필드 분석", analyze)).pack(anchor="w", pady=5)

    def selected_source(table):
        selected = table.selection()
        if not selected:
            raise ValueError("Jira 필드 행을 선택하세요.")
        return selected[0]

    def toggle():
        source = selected_source(tables[0])
        scope = current_scope()
        wizard.select_field(scope, source, not rows()[source].get("selected", False))
        refresh()

    btns = ttk.Frame(auto_tab)
    btns.pack(fill="x", pady=8)
    ttk.Button(btns, text="선택 / 선택 해제", command=lambda: guarded("선택 변경", toggle)).pack(side="left", padx=3)
    ttk.Button(btns, text="전체 제외", command=lambda: guarded("전체 제외", lambda: (wizard.select_all_fields(current_scope(), False), refresh()))).pack(side="left", padx=3)
    ttk.Button(btns, text="수동 필드 연결로 이동", command=lambda: tabs.select(manual_tab)).pack(side="right", padx=3)

    editor = ttk.Frame(manual_tab)
    editor.pack(fill="x", pady=8)
    ttk.Label(editor, text="처리 방법").grid(row=0, column=0, sticky="w")
    ttk.Label(editor, text="대상 Codebeamer 필드 / 생성 타입").grid(row=0, column=1, sticky="w", padx=8)
    action = ttk.Combobox(editor, state="readonly", values=["무시", "기존 필드 연결", "새 필드 생성 요청"], width=24)
    action.grid(row=1, column=0, sticky="w")
    target_box = ttk.Combobox(editor, state="readonly", width=60)
    target_box.grid(row=1, column=1, sticky="w", padx=8)
    field_name = tk.StringVar()
    ttk.Label(editor, text="새 필드 이름 (생성 요청 시)").grid(row=2, column=0, sticky="w", pady=9)
    ttk.Entry(editor, textvariable=field_name, width=45).grid(row=2, column=1, sticky="w", padx=8)
    multiple = tk.BooleanVar(value=False)
    ttk.Checkbutton(editor, text="다중 선택", variable=multiple).grid(row=3, column=1, sticky="w")

    available = []
    def update_editor(event=None):
        source = selected_source(tables[1])
        entry = wizard.plan["scopes"][current_scope()]
        field_name.set(entry["analysis"]["fields"][source].get("name", source))
        source_kind, is_multiple = semantic_type(entry["analysis"]["fields"][source], True)
        multiple.set(bool(is_multiple))
        if action.get() == "기존 필드 연결":
            available[:] = [c for c in wizard.mapping_candidates(current_scope(), source)
                            if not hidden(c["field"])]
            target_box.configure(values=[
                str(field_identifier(c["field"])) + " · " + str(c["field"].get("label", c["field"].get("name", "")))
                + (" [" + c["reason"] + "]" if c["reason"] else "") for c in available])
        elif action.get() == "새 필드 생성 요청":
            entry = wizard.plan["scopes"][current_scope()]
            try:
                profiles = wizard.creation_profiles(current_scope())
            except Exception as exc:
                profiles = {}
                status.set("생성 타입 정의 확인 필요: " + str(exc)[:220])
            available[:] = list(profiles)
            target_box.configure(values=available)
            preferred = "option" if source_kind in ("option", "status") else source_kind
            if preferred in available:
                target_box.set(preferred)
            else:
                target_box.set("")
        else:
            available.clear()
            target_box.configure(values=[])
            target_box.set("")

    tables[1].bind("<<TreeviewSelect>>", lambda event: update_editor() if tables[1].selection() else None)
    action.bind("<<ComboboxSelected>>", update_editor)

    def save_decision():
        source = selected_source(tables[1])
        scope = current_scope()
        choice = action.get()
        if choice == "무시":
            wizard.select_field(scope, source, False)
        elif choice == "기존 필드 연결":
            index = target_box.current()
            if index < 0:
                raise ValueError("기존 Codebeamer 필드를 선택하세요.")
            field = available[index]["field"]
            fid = field_identifier(field)
            wizard.manual_mapping(scope, source, fid, "cb_field_" + str(fid), {})
            wizard.select_field(scope, source, True)
        elif choice == "새 필드 생성 요청":
            semantic = target_box.get()
            if semantic not in available:
                raise ValueError("검증된 Codebeamer 생성 타입을 선택하세요.")
            entry = wizard.plan["scopes"][scope]
            definition = entry["analysis"]["fields"][source]
            values = entry["analysis"].get("values", {}).get(source, [])
            observed = source_choices(values + definition.get("allowedValues", []))
            choices = observed if semantic in ("option", "status") else {}
            wizard.configure_creation(scope, source, field_name.get(), semantic, multiple.get(), choices)
            wizard.select_field(scope, source, True)
        else:
            raise ValueError("무시/기존 필드 연결/새 필드 생성 요청을 선택하세요.")
        refresh()

    ttk.Button(editor, text="선택 저장", command=lambda: guarded("필드 선택", save_decision)).grid(row=4, column=1, sticky="e", pady=9)
    ttk.Label(manual_tab, text="Test Set / Test Plan / Test Execution / Test Run 관계는 일반 텍스트 필드가 아니라 별도 Reference/Xray 관계 검증 대상입니다.").pack(anchor="w")

    def finish():
        count = sum(1 for row in rows().values() if row.get("selected"))
        messagebox.showinfo("매핑 저장", "선택한 필드 " + str(count) + "개가 저장되었습니다. 실제 서버 쓰기는 별도 Import 승인 후 실행합니다.")

    def import_approved():
        review = wizard.visibility_review()
        if not messagebox.askyesno("실제 Import 승인", "선택한 데이터와 신규 필드가 Codebeamer 서버에 생성/변경될 수 있습니다.\n실행 전 원본·대상 설정과 선택한 매핑을 검토했습니까?"):
            return
        result = wizard.execute(approved=True, visibility_review=review)
        if result.get("complete"):
            messagebox.showinfo("Import", "Import 완료. 상세 결과는 실행 상태 로그에서 확인하세요.")
        else:
            messagebox.showwarning("Import", "Import 미완료. 상세 오류 로그를 확인하세요.")

    bottom = ttk.Frame(manual_tab)
    bottom.pack(fill="x", pady=8)
    ttk.Button(bottom, text="완료 · 매핑 저장", command=lambda: guarded("매핑 저장", finish)).pack(side="left")
    ttk.Button(bottom, text="실제 Import 실행…", command=lambda: guarded("Import", import_approved)).pack(side="right")
    root.mainloop()
