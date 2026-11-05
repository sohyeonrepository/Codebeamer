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
    result_tab = ttk.Frame(tabs, padding=16)
    tabs.add(project_tab, text="1. 프로젝트 연결")
    tabs.add(auto_tab, text="2. 데이터 가져오기")
    tabs.add(result_tab, text="3. Import 결과 / 로그")
    ttk.Label(root, textvariable=status, wraplength=1080).pack(fill="x", padx=16, pady=(0, 12))

    def guarded(label, fn):
        try:
            result = fn()
            if not status.get().startswith("생성 타입 정의 확인 필요"):
                status.set(label + " 완료")
            return result
        except Exception as exc:
            message = str(exc)
            if len(message) > 600:
                message = message[:600] + "… (상세 진단 확인)"
            status.set(label + " 실패: " + message)
            messagebox.showerror(label + " 실패", message)
            return None

    # Keep the human-readable execution log separate from raw diagnostics JSON.
    import datetime
    ttk.Label(result_tab, text="Import 결과 / 로그", font=("", 19, "bold")).pack(anchor="w", pady=(0, 8))
    ttk.Label(result_tab, text="선택 필드, 실행 단계, API 오류와 결과를 요약합니다. 자세한 기록은 상태 폴더에 저장됩니다.").pack(anchor="w")
    panel = ttk.Frame(result_tab)
    panel.pack(fill="both", expand=True, pady=12)
    log_text = tk.Text(panel, wrap="word", state="disabled")
    log_scroll = ttk.Scrollbar(panel, orient="vertical", command=log_text.yview)
    log_text.configure(yscrollcommand=log_scroll.set)
    log_text.pack(side="left", fill="both", expand=True)
    log_scroll.pack(side="right", fill="y")

    def append_log(stage, detail):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_text.configure(state="normal")
        log_text.insert("end", "[" + stamp + "] " + stage + " | " + str(detail)[:550] + "\n")
        log_text.see("end")
        log_text.configure(state="disabled")
        root.update_idletasks()

    def summarize_report(report):
        if not isinstance(report, dict):
            append_log("결과", "Import 반환값 형식 확인 필요")
            return
        counts = report.get("counts") or {}
        if isinstance(counts, dict):
            append_log("처리 건수", ", ".join(str(k) + "=" + str(v) for k, v in counts.items()))
        for record in report.get("issues", []):
            if isinstance(record, dict):
                append_log("Issue", str(record.get("key", "대상")) + " / " + str(record.get("status", "미확인")))
        errors = report.get("errors") or report.get("blockers") or []
        for error in errors:
            if isinstance(error, dict):
                append_log("오류", str(error.get("source", "Import")) + " / " +
                           str(error.get("error_code", "ERROR")) + " / " +
                           str(error.get("error") or error.get("reason") or error.get("error_detail") or "진단 로그 확인"))
        append_log("결과", "성공" if report.get("complete") else "미완료 / 오류 " + str(len(errors)) + "건")
        append_log("로그 위치", str(wizard.state.root / "report.json"))

    def clear_log():
        log_text.configure(state="normal")
        log_text.delete("1.0", "end")
        log_text.configure(state="disabled")

    ttk.Button(result_tab, text="로그 지우기", command=clear_log).pack(anchor="e")
    # Visible deployment marker: the active Windows source can be checked
    # without requiring Git, a terminal, or exposing credentials.
    from pathlib import Path
    source_file = str(Path(__file__).resolve())
    engine_file = str((Path(__file__).resolve().parent / "migration" / "engine.py"))
    from migration import simple_item_import
    append_log("실행 버전", "simple-item-import-v1 · 단일 Issue 빠른 생성 경로")
    append_log("실행 경로", source_file)
    append_log("엔진 경로", str(Path(simple_item_import.__file__).resolve()))
    import inspect
    # A recent Engine file does not prove preservation.py was also updated.
    # Check the exact preflight function that execute() imports at runtime.
    from migration import preservation
    from migration.preservation import preflight
    preservation_source = inspect.getsource(preflight)
    required_section = preservation_source.split("required =", 1)[1].split("for name in required:", 1)[0] if "required =" in preservation_source else ""
    legacy_lookup_preflight = ("'lookup'" in required_section or '"lookup"' in required_section)
    append_log("검증 모듈", str(Path(preservation.__file__).resolve()))
    append_log("검증 코드", "구버전 preservation.py · lookup 필수 검사 감지" if legacy_lookup_preflight
               else "신규 preservation.py · lookup 필수 검사 없음")
    append_log("Import 모드", str(wizard.config.get("migration", {}).get("mode", "미확인")))

    append_log("준비", "Import 대기 중 · 프로젝트와 필드를 선택하세요.")

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
        scope_names.clear()
        scope_box.configure(values=[])
        scope_box.set("")
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

    # Single field-import page. Checkboxes are intentionally unchecked on load.
    ttk.Label(auto_tab, text="데이터 가져오기", font=("", 19, "bold")).pack(anchor="w")
    ttk.Label(auto_tab, text="왼쪽 체크박스로 이관 범위를 선택하고, CB 대상 필드는 오른쪽 드롭다운에서 지정하세요.").pack(anchor="w", pady=(4, 10))

    toolbar = ttk.Frame(auto_tab)
    toolbar.pack(fill="x", pady=6)
    ttk.Label(toolbar, text="필드 필터").pack(side="left", padx=(0, 8))
    filter_var = tk.StringVar(value="현재 사용 중인 필드")
    filter_box = ttk.Combobox(toolbar, textvariable=filter_var, state="readonly", width=24,
                              values=["현재 사용 중인 필드", "전체 필드"])
    filter_box.pack(side="left")
    shown_count = tk.StringVar(value="0개")
    ttk.Label(toolbar, textvariable=shown_count).pack(side="left", padx=12)
    ttk.Button(toolbar, text="필드 다시 가져오기", command=lambda: guarded("필드 분석", analyze)).pack(side="right", padx=5)

    cols = ("check", "jira", "cb", "type")
    frame = ttk.Frame(auto_tab)
    frame.pack(fill="both", expand=True, pady=(7, 9))
    table = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
    for key, title, width in (
        ("check", "선택", 60), ("jira", "Jira 필드", 305),
        ("cb", "Codebeamer 필드  ▾", 480), ("type", "Jira 타입", 130)):
        table.heading(key, text=title)
        table.column(key, width=width, stretch=(key != "check"), anchor="center" if key == "check" else "w")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    table.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    editor = [None]

    def rows():
        return wizard.plan["scopes"][current_scope()]["rows"]

    def used_field(info):
        # A configured field belongs to the selected Jira screen/configuration;
        # observed non-null values also count as usage. Do not infer usage from
        # global Jira fields that only appear as null in API responses.
        return bool(info.get("configured") or info.get("has_value") or info.get("required"))

    def refresh():
        if editor[0] is not None:
            editor[0].destroy()
            editor[0] = None
        entry = wizard.plan["scopes"][current_scope()]
        table.delete(*table.get_children())
        count = 0
        for source, row in entry.get("rows", {}).items():
            info = entry["analysis"]["fields"].get(source, {})
            if filter_var.get() == "현재 사용 중인 필드" and not used_field(info):
                continue
            target = row.get("target") or {}
            if row.get("creation"):
                target_name = str(row["creation"].get("label", source)) + " (신규 생성 예정)"
            else:
                target_name = target.get("label") or target.get("name") or "대상 필드 선택 ▾"
            kind = semantic_type(info, True)[0] or "미확인"
            table.insert("", "end", iid=source, values=(
                "☑" if row.get("selected", False) else "☐",
                info.get("name") or source, target_name, kind))
            count += 1
        shown_count.set(str(count) + "개 표시 / " + str(len(entry.get("rows", {}))) + "개 분석")

    def analyze():
        scope = current_scope()
        wizard.analyze(scope)
        # The user's default is always unchecked after collecting source fields.
        wizard.select_all_fields(scope, False)
        refresh()
        tabs.select(auto_tab)

    ttk.Button(project_tab, text="완료 · 데이터 가져오기", command=lambda: guarded("필드 분석", analyze)).pack(anchor="w", pady=8)
    filter_box.bind("<<ComboboxSelected>>", lambda event: guarded("필드 필터", refresh))

    def toggle(source):
        scope = current_scope()
        wizard.select_field(scope, source, not rows()[source].get("selected", False))
        refresh()

    def select_visible(value):
        scope = current_scope()
        for source in table.get_children():
            wizard.plan["scopes"][scope]["rows"][source]["selected"] = bool(value)
        wizard.state.save("wizard", "plan", wizard.plan)
        refresh()

    def creation_popup(source):
        scope = current_scope()
        entry = wizard.plan["scopes"][scope]
        info = entry["analysis"]["fields"][source]
        profiles = wizard.creation_profiles(scope)
        if not profiles:
            raise ValueError("해당 Tracker에 검증된 신규 필드 생성 타입이 없습니다.")
        window = tk.Toplevel(root)
        window.title("신규 Codebeamer 필드 생성 설정")
        window.geometry("680x570")
        window.transient(root)
        window.grab_set()
        content = ttk.Frame(window, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Jira 필드: " + str(info.get("name", source)),
                  font=("", 12, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Label(content, text="신규 필드 이름").pack(anchor="w")
        label_var = tk.StringVar(value=str(info.get("name", source)))
        ttk.Entry(content, textvariable=label_var, width=65).pack(anchor="w", pady=(3, 10))
        ttk.Label(content, text="Codebeamer 필드 타입").pack(anchor="w")
        type_var = tk.StringVar()
        type_box = ttk.Combobox(content, state="readonly", textvariable=type_var,
                                values=list(profiles), width=44)
        type_box.pack(anchor="w", pady=(3, 10))
        kind, is_multi = semantic_type(info, True)
        preferred = "option" if kind in ("option", "status") else kind
        if preferred in profiles:
            type_var.set(preferred)
        multi_var = tk.BooleanVar(value=bool(is_multi))
        ttk.Checkbutton(content, text="다중 선택", variable=multi_var).pack(anchor="w", pady=5)
        ttk.Label(content, text="Choice 옵션 (원본 값에 맞춰 자동 입력, 필요하면 수정)").pack(anchor="w", pady=(8, 2))
        choices_box = tk.Text(content, height=7, wrap="none", width=65)
        choices_box.pack(fill="x")
        observed = entry["analysis"].get("values", {}).get(source, [])
        allowed = info.get("allowedValues") or []
        values = (observed if isinstance(observed, list) else []) + (allowed if isinstance(allowed, list) else [])
        proposed_choices = source_choices(values)
        for identity, label in proposed_choices.items():
            choices_box.insert("end", str(identity) + " = " + str(label) + "\n")
        ttk.Label(content, text="Reference 대상 Tracker (Reference 타입인 경우)").pack(anchor="w", pady=(9, 2))
        tracker_items = wizard.plan["projects"][entry["project"]]["trackers"]
        tracker_box = ttk.Combobox(content, state="readonly", width=56,
                                   values=[str(t["id"]) + " · " + t.get("name", "") for t in tracker_items])
        tracker_box.pack(anchor="w")
        ttk.Label(content, text="여기서는 생성 계획만 저장합니다. 실제 필드 생성은 Import 승인 시 실행됩니다.",
                  wraplength=610).pack(anchor="w", pady=12)

        def save_new():
            def perform():
                semantic = type_var.get()
                if semantic not in profiles:
                    raise ValueError("Codebeamer 필드 타입을 선택하세요.")
                mapping = {}
                if semantic in ("option", "status"):
                    for line in choices_box.get("1.0", "end").splitlines():
                        if not line.strip():
                            continue
                        if "=" not in line:
                            raise ValueError("Choice 옵션은 '원본값 = 대상값' 형태로 입력하세요.")
                        identity, label = line.split("=", 1)
                        if not identity.strip() or not label.strip():
                            raise ValueError("Choice 값은 비울 수 없습니다.")
                        mapping[identity.strip()] = label.strip()
                reference_tracker = None
                if semantic == "reference":
                    if tracker_box.current() < 0:
                        raise ValueError("참조 대상 Tracker를 선택하세요.")
                    reference_tracker = tracker_items[tracker_box.current()]["id"]
                wizard.configure_creation(scope, source, label_var.get(), semantic,
                                          multi_var.get(), mapping, reference_tracker=reference_tracker)
                wizard.select_field(scope, source, True)
                refresh()
                window.destroy()
            guarded("신규 필드 설정", perform)

        ttk.Button(content, text="설정 저장", command=save_new).pack(side="right", pady=8)
        ttk.Button(content, text="취소", command=window.destroy).pack(side="right", padx=10, pady=8)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

    def open_target_dropdown(source):
        if editor[0] is not None:
            editor[0].destroy()
            editor[0] = None
        scope = current_scope()
        candidates = [c for c in wizard.mapping_candidates(scope, source) if not hidden(c["field"])]
        choices = ["— 대상 미지정 —"]
        for candidate in candidates:
            field = candidate["field"]
            name = field.get("label") or field.get("name") or "Field"
            choices.append(str(name) + " · ID " + str(field_identifier(field)))
        choices.append("＋ 신규 필드 생성…")
        bounds = table.bbox(source, "cb")
        if not bounds:
            return
        x, y, width, height = bounds
        dropdown = ttk.Combobox(table, state="readonly", values=choices)
        editor[0] = dropdown
        dropdown.place(x=x, y=y, width=width, height=max(height, 24))
        dropdown.focus_set()

        def changed(event=None):
            index = dropdown.current()
            dropdown.destroy()
            editor[0] = None
            if index == 0:
                wizard.select_field(scope, source, False)
                refresh()
            elif index == len(choices) - 1:
                guarded("신규 필드 생성", lambda: creation_popup(source))
            elif 0 < index <= len(candidates):
                field = candidates[index - 1]["field"]
                fid = field_identifier(field)
                # Summary/Description map to native Item properties, not customFields.
                target_key = ("name" if source == "summary" else
                              "description" if source == "description" else
                              "cb_field_" + str(fid))
                guarded("기존 필드 연결",
                        lambda: (wizard.manual_mapping(scope, source, fid, target_key, {}),
                                 refresh()))
        dropdown.bind("<<ComboboxSelected>>", changed)
        dropdown.bind("<Escape>", lambda event: (dropdown.destroy(), editor.__setitem__(0, None)))

    def table_clicked(event):
        source = table.identify_row(event.y)
        column = table.identify_column(event.x)
        if source not in table.get_children():
            return
        if column == "#1":
            guarded("선택 변경", lambda: toggle(source))
            return "break"
        if column == "#3":
            guarded("대상 필드 선택", lambda: open_target_dropdown(source))
            return "break"

    table.bind("<Button-1>", table_clicked)
    ttk.Label(auto_tab, text="Test Set / Test Plan / Test Execution / Test Run 관계는 Xray/Reference 별도 설정이 필요합니다.").pack(anchor="w")
    buttons = ttk.Frame(auto_tab)
    buttons.pack(fill="x", pady=10)
    ttk.Button(buttons, text="보이는 필드 전체 체크", command=lambda: guarded("전체 선택", lambda: select_visible(True))).pack(side="left")
    ttk.Button(buttons, text="보이는 필드 체크 해제", command=lambda: guarded("전체 해제", lambda: select_visible(False))).pack(side="left", padx=8)

    def finish():
        count = sum(bool(r.get("selected")) for r in rows().values())
        messagebox.showinfo("매핑 저장", str(count) + "개 필드가 선택되었습니다.\n실제 변경은 별도 Import 승인이 있어야 수행됩니다.")

    def import_approved():
        tabs.select(result_tab)
        previous_stage_count = len(wizard.plan.get("stages", []))
        def log_identity_stages():
            for scope_key in wizard.plan.get("scopes", {}):
                evidence = wizard.state.load("wizard-identity-resolution", scope_key)
                if isinstance(evidence, dict):
                    append_log("Jira Key 판정", str(scope_key) + " · " +
                               str(evidence.get("status", "미확인")) + " · Field ID " +
                               str(evidence.get("field_id", "생성 전")))
            for entry in wizard.plan.get("stages", [])[previous_stage_count:]:
                name = str(entry.get("stage") or "")
                result = entry.get("result") or {}
                if name.startswith("identity-field:") and isinstance(result,dict):
                    append_log("Jira Key 필드", "생성 후 조회 확인 · CB Field ID " +
                               str(result.get("field_id", "미확인")))
                elif name.startswith("identity-ready:") and isinstance(result,dict):
                    append_log("Jira Key 전달", "검증 완료 · Tracker " +
                               str(result.get("tracker_id", "미확인")) +
                               " / Field ID " + str(result.get("jira_key_field_id", "미확인")))
        
        append_log("Import", "사용자 승인 대기")
        try:
            scope = current_scope()
            selected = [(name, row) for name, row in rows().items() if row.get("selected", False)]
            append_log("범위", str(scope) + " · 체크된 필드 " + str(len(selected)) + "개")
            for name, row in selected:
                spec = row.get("spec") or {}
                target = (row.get("creation") or {}).get("label") or spec.get("target") or "매핑 확인 필요"
                append_log("매핑", name + " → " + target)
            review = wizard.visibility_review()
            if not messagebox.askyesno("실제 Import 승인",
                "선택한 Jira Issue 1건을 기존 Codebeamer Tracker에 생성합니다.\n기존 Jira Key가 있으면 중복 생성하지 않습니다. 계속할까요?"):
                append_log("Import", "사용자가 실행을 취소했습니다.")
                return
            append_log("Import", "단일 Issue 빠른 생성 경로 시작 · 신규 Tracker/필드 생성 없음")
            from migration.simple_item_import import run as run_simple_item_import
            report = run_simple_item_import(wizard, progress=append_log)
            summarize_report(report)
        except Exception as exc:
            log_identity_stages()
            from migration.diagnostics import safe_exception_details, write_diagnostic
            details = safe_exception_details(exc)
            frames = details.get("frames") or []
            if frames:
                frame = frames[-1]
                append_log("오류 호출 위치", str(frame.get("file")) + ":" +
                           str(frame.get("line")) + " · " + str(frame.get("function")))
            try:
                filepath = write_diagnostic(wizard, "Import", exc)
                append_log("상세 진단", filepath)
            except Exception as logging_error:
                append_log("로그 저장 오류", type(logging_error).__name__)
            append_log("Import 실패", str(exc) if isinstance(exc, Exception) else type(exc).__name__)
            status.set("Import 실패: " + str(exc)[:250])

    def advanced():
        # The compact UI owns its own relation editor; never launch the legacy UI.
        dialog = tk.Toplevel(root)
        dialog.title("고급 설정 · Jira Issue Link / CB Association")
        dialog.geometry("730x470")
        dialog.transient(root)
        dialog.grab_set()
        content = ttk.Frame(dialog, padding=18)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="고급 설정 · 관계 유형 매핑", font=("", 16, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Label(content, text="Jira Issue Link Type을 Codebeamer Association Type으로 연결합니다. 이 화면은 관계 타입 설정만 저장합니다.",
                  wraplength=680).pack(anchor="w")
        ttk.Label(content, text="Xray Test Run 및 Reference 필드 생성/연결은 별도 검증이 필요하며 여기서 변경하지 않습니다.",
                  wraplength=680).pack(anchor="w", pady=5)
        relation_types = {"jira": [], "codebeamer": []}
        source_box = ttk.Combobox(content, state="readonly", width=80)
        target_box = ttk.Combobox(content, state="readonly", width=80)
        ttk.Label(content, text="Jira Issue Link Type").pack(anchor="w", pady=(15, 3))
        source_box.pack(anchor="w")
        ttk.Label(content, text="Codebeamer Association Type").pack(anchor="w", pady=(12, 3))
        target_box.pack(anchor="w")
        reverse = tk.BooleanVar(value=False)
        ttk.Checkbutton(content, text="방향 반전", variable=reverse).pack(anchor="w", pady=10)
        advanced_status = tk.StringVar(value="관계 유형 조회를 선택하세요.")
        ttk.Label(content, textvariable=advanced_status, wraplength=675).pack(anchor="w", pady=8)

        def load_catalog():
            catalog = wizard.relation_catalog()
            relation_types["jira"] = catalog["jira"]
            relation_types["codebeamer"] = catalog["codebeamer"]
            source_box.configure(values=[str(t["id"]) + " · " + str(t["name"]) for t in catalog["jira"]])
            target_box.configure(values=[str(t["id"]) + " · " + str(t["name"]) for t in catalog["codebeamer"]])
            advanced_status.set("Jira " + str(len(catalog["jira"])) + "개 / CB " +
                                str(len(catalog["codebeamer"])) + "개 유형 조회 완료")

        def load_existing(event=None):
            if source_box.current() < 0:
                return
            source = relation_types["jira"][source_box.current()]
            selected = wizard.config["migration"].get("wizard", {}).get("association_types", {}).get(str(source["id"]))
            target_box.set("")
            reverse.set(False)
            if isinstance(selected, dict) and selected.get("mode", "association") == "association":
                for index, item in enumerate(relation_types["codebeamer"]):
                    if item["id"] == selected.get("type_id"):
                        target_box.current(index)
                        reverse.set(bool(selected.get("reverse", False)))
                        break

        def save_relation():
            if source_box.current() < 0 or target_box.current() < 0:
                raise ValueError("Jira 및 CB 관계 유형을 선택하세요.")
            source = relation_types["jira"][source_box.current()]
            target = relation_types["codebeamer"][target_box.current()]
            wizard.save_relation_mapping(source["id"], {
                "mode": "association", "type_id": target["id"], "reverse": reverse.get()
            })
            advanced_status.set(str(source["name"]) + " → " + str(target["name"]) + " 저장 완료")

        source_box.bind("<<ComboboxSelected>>", load_existing)
        controls = ttk.Frame(content)
        controls.pack(fill="x", pady=15)
        ttk.Button(controls, text="관계 유형 조회", command=lambda: guarded("관계 조회", load_catalog)).pack(side="left")
        ttk.Button(controls, text="매핑 저장", command=lambda: guarded("관계 저장", save_relation)).pack(side="left", padx=10)
        ttk.Button(controls, text="닫기", command=dialog.destroy).pack(side="right")
    ttk.Button(buttons, text="완료 · 매핑 저장", command=lambda: guarded("매핑 저장", finish)).pack(side="right", padx=6)
    ttk.Button(buttons, text="실제 Import 실행…", command=lambda: guarded("Import", import_approved)).pack(side="right", padx=6)
    ttk.Button(buttons, text="Xray / Reference 고급 설정", command=advanced).pack(side="right", padx=6)
    root.mainloop()
