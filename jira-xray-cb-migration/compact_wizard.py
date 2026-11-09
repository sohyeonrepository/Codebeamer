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
            if result is not False and not status.get().startswith("생성 타입 정의 확인 필요"):
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
    ttk.Label(toolbar,text="필드 검색").pack(side="left",padx=(12,4))
    field_search=tk.StringVar()
    search_box=ttk.Entry(toolbar,textvariable=field_search,width=25)
    search_box.pack(side="left")
    ttk.Label(toolbar,text="Jira Issue Key").pack(side="left",padx=(12,4))
    import_issue_key=tk.StringVar()
    ttk.Entry(toolbar,textvariable=import_issue_key,width=20).pack(side="left")
    shown_count = tk.StringVar(value="0개")
    ttk.Label(toolbar, textvariable=shown_count).pack(side="left", padx=12)

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
            needle=field_search.get().strip().casefold()
            if needle and needle not in " ".join((
                str(source),str(info.get("name") or ""),
                str((row.get("target") or {}).get("label") or ""),
                str((row.get("target") or {}).get("name") or "")
            )).casefold():
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
        from migration.simple_item_import import _field
        tracker=wizard.plan["scopes"][scope].get("tracker")
        if type(tracker) is not int:
            raise ValueError("기존 CB Tracker를 먼저 연결하세요.")
        try:
            jira_key_id=_field(wizard.cb,tracker)
        except Exception as exc:
            append_log("Jira Key 필드 진단", "Tracker "+str(tracker)+" · "+str(exc))
            raise ValueError("Jira Key 필드 확인 실패: "+str(exc)) from None
        append_log("Jira Key 확인","Tracker "+str(tracker)+
                   " · Jira Key Field ID "+str(jira_key_id))
        wizard.analyze(scope)
        # The user's default is always unchecked after collecting source fields.
        wizard.select_all_fields(scope, False)
        refresh()
        tabs.select(auto_tab)

    ttk.Button(project_tab, text="완료 · 데이터 가져오기", command=lambda: guarded("필드 분석", analyze)).pack(anchor="w", pady=8)
    filter_box.bind("<<ComboboxSelected>>", lambda event: guarded("필드 필터", refresh))
    field_search.trace_add("write",lambda *_: refresh() if active_scope[0] in wizard.plan.get("scopes",{}) and "rows" in wizard.plan["scopes"][active_scope[0]] else None)

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
        ttk.Label(content, text="Choice-Option/User는 승인 후 실제 필드 생성과 Field ID 검증을 진행합니다. User 계정 생성과는 별개입니다.",
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
                # The proposed name can already exist (for example, a prior
                # create request succeeded but its local receipt was lost).
                # Never create a duplicate or silently adopt an unrelated field.
                if semantic in ("option","status","user"):
                    from migration.wizard import target_fields,compatible
                    from migration.option_batch import norm
                    selected_name=label_var.get().strip()
                    current_fields=target_fields(wizard.cb,entry["tracker"])
                    same=[f for f in current_fields if
                          norm(f.get("label") or f.get("name"))==norm(selected_name)]
                    if same:
                        if len(same)!=1:
                            raise ValueError("동일 이름의 CB 필드가 여러 개입니다. 수동 확인이 필요합니다: "+selected_name)
                        existing_field=same[0]
                        existing_id=field_identifier(existing_field)
                        expected_ref="UserReference" if semantic=="user" else "ChoiceOptionReference"
                        expected_kind="user" if semantic=="user" else "option"
                        if (type(existing_id) is not int or
                            existing_field.get("referenceType")!=expected_ref or
                            not str(existing_field.get("valueModel") or "").startswith("ChoiceFieldValue") or
                            not compatible(expected_kind,multi_var.get(),existing_field)):
                            raise ValueError("동일 이름 CB 필드가 있지만 Choice 참조 유형/다중 설정이 다릅니다: "+
                                             selected_name+" · ID "+str(existing_id)+
                                             ". 덮어쓰거나 자동 재사용하지 않습니다.")
                        if not messagebox.askyesno("기존 필드 확인",
                            "CB에 동일 이름의 Choice 필드가 존재합니다.\\n"+
                            "이름: "+selected_name+"\\nField ID: "+str(existing_id)+
                            "\\n참조 유형: "+expected_ref+
                            "\\n다중 선택: "+str(multi_var.get())+
                            "\\n\\n새로 생성하지 않고 기존 필드에 연결할까요?",parent=window):
                            append_log("신규 Choice 중단",source+" · 같은 이름의 CB 필드 "+
                                       str(existing_id)+" 존재 · 변경 없음")
                            return False
                        # Reuse only after explicit approval. Existing options
                        # may be empty; let the Choice editor add missing ones
                        # under its separate approval/readback workflow.
                        entry["target_fields"]=current_fields
                        wizard.manual_mapping(scope,source,existing_id,
                                              "cb_field_"+str(existing_id),{})
                        wizard.select_field(scope,source,True)
                        append_log("기존 Choice 재사용",source+" → CB "+str(existing_id)+
                                   " · 동일 이름/타입/다중 확인; 신규 필드 생성 생략")
                        refresh()
                        window.destroy()
                        if semantic in ("option","status"):
                            option_mapping_popup(source)
                        return True
                wizard.configure_creation(scope, source, label_var.get(), semantic,
                                          multi_var.get(), mapping, reference_tracker=reference_tracker)
                wizard.select_field(scope, source, True)
                if semantic in ("option","status","user"):
                    detail_message=("와 옵션 "+str(len(set(mapping.values())))+"개" if semantic in ("option","status")
                                    else " (Choice-UserReference, 사용자 계정 생성 아님)")
                    if not messagebox.askyesno("신규 필드 실제 생성",
                        "CB Tracker "+str(entry["tracker"])+"에 신규 필드 '"+label_var.get()+
                        "'"+detail_message+
                        "를 실제 생성합니다. 계속할까요?",parent=window):
                        append_log("신규 Choice 생성 취소",source+" · 서버 변경 없음 · 생성 완료 아님")
                        status.set("신규 필드 실제 생성 취소 · 서버 변경 없음")
                        refresh()
                        window.destroy()
                        return False
                    append_log("신규 Choice 생성 시작",source+" · type="+semantic+
                               " · multiple="+str(multi_var.get())+
                               " · 옵션 "+str(len(set(mapping.values())))+"개")
                    from migration.option_batch import norm
                    spec=wizard.create_field(scope,source,entry["rows"][source],entry["tracker"])
                    fid=spec.get("field_id")
                    if type(fid) is not int:
                        raise ValueError("신규 필드 실제 CB ID를 확인하지 못했습니다.")
                    actual=wizard.cb.http.request("GET",
                        "/v3/trackers/"+str(entry["tracker"])+"/fields/"+str(fid))
                    # A returned ID or a locally saved creation plan is not proof
                    # that the Tracker actually contains the field.
                    configuration=wizard.cb.configuration(entry["tracker"])
                    config_fields=configuration.get("fields") if isinstance(configuration,dict) else None
                    configured=[f for f in (config_fields or []) if isinstance(f,dict)
                                and field_identifier(f)==fid]
                    if (not isinstance(actual,dict) or field_identifier(actual)!=fid or
                        not isinstance(config_fields,list) or len(configured)!=1):
                        append_log("신규 필드 서버 검증 실패",source+" · CB Field ID "+str(fid)+
                                   " · 상세 GET/Tracker Configuration 일치 여부 확인 필요")
                        raise ValueError("서버 응답에 Field ID "+str(fid)+"가 있지만 Tracker Configuration과 "+
                                         "상세 GET 양쪽에서 동일 필드를 확인하지 못했습니다. "+
                                         "생성 완료로 처리하지 않습니다. 재생성 전에 서버 상태를 확인하세요.")
                    append_log("신규 필드 서버 확인",source+" · CB Field ID "+str(fid)+
                               " · 상세 GET 및 Tracker Configuration 확인")
                    if semantic=="user":
                        if (not isinstance(actual,dict) or field_identifier(actual)!=fid or
                            actual.get("referenceType")!="UserReference" or
                            not str(actual.get("valueModel") or "").startswith("ChoiceFieldValue")):
                            raise ValueError("생성된 필드가 Choice-UserReference가 아닙니다. 서버에서 확인하세요.")
                        spec["target"]="cb_field_"+str(fid)
                        spec["user_identity_lookup"]=True
                        spec.pop("missing_definition",None)
                        spec.pop("selection_unverified",None)
                        entry["rows"][source].update(spec=spec,target=actual,
                                                    mapping_mode="manual",status="mapped")
                        entry["rows"][source].pop("creation",None)
                        entry["rows"][source].pop("creation_saved",None)
                        wizard.state.save("wizard","plan",wizard.plan)
                        append_log("신규 User 필드 검증",source+" → CB "+str(fid)+
                                   " · 사용자 자동 매핑 연결")
                        refresh()
                        window.destroy()
                        return True
                    if (actual.get("referenceType")!="ChoiceOptionReference" or
                        not str(actual.get("valueModel") or "").startswith("ChoiceFieldValue")):
                        raise ValueError("신규 필드 ID "+str(fid)+"는 Choice-Option 타입이 아닙니다. "+
                                         "필드 생성 성공으로 처리하지 않습니다.")
                    existing={norm(o.get("name")):o for o in actual.get("options",[])
                              if isinstance(o,dict) and type(o.get("id")) is int}
                    if any(norm(name) not in existing for name in mapping.values()):
                        raise ValueError("신규 필드 생성 후 CB 옵션 ID 검증 실패. 재생성하지 말고 서버 확인 필요.")
                    # The Issue import adapter uses Jira displayed option names,
                    # while creation stores Jira option identities.
                    from migration.option_batch import choice_names
                    aliases={}
                    for item in values:
                        try:names=choice_names(item,source)
                        except Exception:continue
                        for name in names:
                            aliases[name]=next((target for original,target in mapping.items()
                                               if norm(original)==norm(name) or
                                               norm(proposed_choices.get(original))==norm(name)),name)
                    name_mapping=dict(mapping)
                    name_mapping.update(aliases)
                    spec["value_mapping"]={k:existing[norm(v)]["name"]
                                           for k,v in name_mapping.items() if norm(v) in existing}
                    spec["choice_options_verified"]=True
                    spec.pop("missing_definition",None)
                    spec.pop("selection_unverified",None)
                    entry["rows"][source].update(spec=spec,target=actual,
                                                mapping_mode="manual",status="mapped")
                    entry["rows"][source].pop("creation",None)
                    entry["rows"][source].pop("creation_saved",None)
                    wizard.state.save("wizard","plan",wizard.plan)
                    append_log("신규 Choice 검증",source+" → CB "+str(fid)+
                               " · 실제 옵션 ID "+str(len(existing))+"개 조회")
                refresh()
                window.destroy()
                return True
            guarded("신규 필드 설정", perform)

        ttk.Button(content, text="설정 저장", command=save_new).pack(side="right", pady=8)
        ttk.Button(content, text="취소", command=window.destroy).pack(side="right", padx=10, pady=8)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

    def option_mapping_popup(source):
        from migration.option_batch import norm
        scope=current_scope()
        entry=wizard.plan["scopes"][scope]
        row=entry["rows"][source]
        spec=row.get("spec") or {}
        fid=spec.get("field_id")
        if type(fid) is not int:
            raise ValueError("먼저 CB Choice 대상 필드를 선택하세요.")
        tracker=entry["tracker"]
        detail=wizard.cb.http.request("GET",
            "/v3/trackers/"+str(tracker)+"/fields/"+str(fid))
        if not isinstance(detail,dict) or detail.get("id")!=fid or (
                detail.get("referenceType")!="ChoiceOptionReference" or
                not str(detail.get("valueModel") or "").startswith("ChoiceFieldValue") or
                not isinstance(detail.get("options"),list)):
            raise ValueError("CB Choice 필드 옵션 상세를 읽을 수 없습니다. Field ID "+str(fid))
        options=[o for o in detail["options"] if isinstance(o,dict) and
                 type(o.get("id")) is int and isinstance(o.get("name"),str)]
        if not options:
            append_log("Choice 옵션 없음",source+" → CB "+str(fid)+
                       " · Jira 값으로 신규 옵션 생성 필요")
        info=entry["analysis"]["fields"].get(source,{})
        from mapping_helper import source_choices
        observed=list(entry["analysis"].get("values",{}).get(source,[]))
        allowed=info.get("allowedValues") or []
        if isinstance(allowed,list):observed.extend(allowed)
        jira_values=source_choices(observed)
        # Option identities may be Jira IDs while issue payloads use names.
        # Include the same field's raw display names as aliases for the POST
        # adapter without guessing or creating any CB option.
        from migration.option_batch import choice_names
        aliases={}
        for sample in observed:
            try:
                names=choice_names(sample,source)
            except Exception:
                continue
            for name in names:
                aliases.setdefault(name,name)
        for name in aliases:
            jira_values.setdefault(name,name)
        append_log("Choice 옵션 조회",str(source)+" → CB "+str(fid)+
                   " · Jira 값 "+str(len(jira_values))+"개 · CB 옵션 "+str(len(options))+"개")
        if not jira_values:
            field_diagnostics(source)
            raise ValueError("Jira 원본 옵션값을 확인할 수 없습니다. Jira 필드 데이터를 조회하세요.")
        dlg=tk.Toplevel(root)
        dlg.title("Choice 옵션 매핑 · "+str(source))
        dlg.geometry("690x540")
        dlg.transient(root)
        dlg.grab_set()
        ttk.Label(dlg,text="Jira 원본 옵션 → CB 대상 옵션",
                  font=("",14,"bold")).pack(anchor="w",padx=16,pady=(15,4))
        ttk.Label(dlg,text="같은 이름은 자동 선택합니다. 없는 옵션은 승인 후 CB Tracker에 생성할 수 있습니다.",
                  wraplength=640).pack(anchor="w",padx=16,pady=(0,10))
        outer=ttk.Frame(dlg)
        outer.pack(fill="both",expand=True,padx=16)
        canvas=tk.Canvas(outer,highlightthickness=0)
        bar=ttk.Scrollbar(outer,orient="vertical",command=canvas.yview)
        inner=ttk.Frame(canvas)
        inner.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0),window=inner,anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left",fill="both",expand=True)
        bar.pack(side="right",fill="y")
        option_names=[o["name"] for o in options]
        saved=dict(spec.get("value_mapping") or {})
        edits={}
        for i,(identity,label) in enumerate(jira_values.items()):
            ttk.Label(inner,text=str(label)+"  ["+str(identity)+"]",width=40).grid(
                row=i,column=0,sticky="w",pady=4)
            variable=tk.StringVar()
            wanted=saved.get(identity,saved.get(label,label))
            matching=[name for name in option_names if norm(name)==norm(wanted)]
            if len(matching)==1:
                variable.set(matching[0])
            elif not matching:
                variable.set(str(label))
            selector=ttk.Combobox(inner,values=list(dict.fromkeys(option_names+[str(label)])),
                                  state="normal",
                                  textvariable=variable,width=34)
            selector.grid(row=i,column=1,sticky="w",pady=4)
            edits[identity]=variable
        def save_choices():
            mapping={}
            requested=[]
            for identity,variable in edits.items():
                target=variable.get().strip()
                if not target:
                    messagebox.showerror("옵션 누락",
                        "Jira 값 "+str(identity)+"의 CB 옵션 이름을 입력하세요.",parent=dlg)
                    return
                matching=[o for o in options if norm(o["name"])==norm(target)]
                if len(matching)>1:
                    messagebox.showerror("중복 옵션","CB 옵션 이름이 중복됩니다: "+target,parent=dlg)
                    return
                mapping[identity]=matching[0]["name"] if matching else target
                requested.append(mapping[identity])
            from migration.choice_option_creation import diff_options,append_missing
            missing=diff_options(detail,requested)
            if missing:
                if not messagebox.askyesno("CB 신규 옵션 실제 생성",
                    "기존 CB 필드 "+str(fid)+"에 다음 옵션을 추가합니다.\\n\\n"+
                    "\\n".join(missing[:35])+
                    ("\\n..." if len(missing)>35 else "")+
                    "\\n\\nTracker 설정 변경을 승인할까요?",parent=dlg):
                    append_log("Choice 옵션 생성 취소",source+" · 생성 전 중단")
                    return
                try:
                    actual=append_missing(wizard.cb,tracker,fid,requested,
                        wizard.state.root,approved=True,progress=append_log)
                except Exception as exc:
                    append_log("Choice 옵션 생성 실패",source+" → CB "+str(fid)+" · "+str(exc))
                    messagebox.showerror("Choice 옵션 생성 실패",str(exc),parent=dlg)
                    return
            else:
                actual=detail
            actual_names={norm(o.get("name")):o["name"] for o in actual.get("options",[])
                          if isinstance(o,dict) and type(o.get("id")) is int}
            if any(norm(name) not in actual_names for name in requested):
                messagebox.showerror("옵션 검증 오류","CB 생성 후 옵션 ID를 재확인하지 못했습니다.",parent=dlg)
                return
            mapping={k:actual_names[norm(v)] for k,v in mapping.items()}
            spec=entry["rows"][source]["spec"]
            spec["value_mapping"]=mapping
            spec["choice_options_verified"]=True
            spec.pop("selection_unverified",None)
            spec.pop("missing_definition",None)
            wizard.state.save("wizard","plan",wizard.plan)
            append_log("Choice 옵션 확정",str(source)+" → CB "+str(fid)+
                       " · 옵션 "+str(len(mapping))+"개 연결")
            dlg.destroy()
            refresh()
        ttk.Button(dlg,text="옵션 매핑 확정 / 없는 옵션 생성",
                   command=save_choices).pack(side="right",padx=16,pady=12)

    def field_diagnostics(source, candidates=None):
        import json
        from pathlib import Path
        from migration.field_semantics import jira_field_semantics
        scope=current_scope()
        entry=wizard.plan["scopes"][scope]
        info=entry["analysis"]["fields"].get(source,{})
        kind,multiple,evidence=jira_field_semantics(info)
        candidates=candidates if candidates is not None else wizard.mapping_candidates(scope,source)
        report={
            "scope":scope,"jira_field_id":source,"jira_name":info.get("name"),
            "jira_schema":info.get("schema"),"jira_allowed_values":info.get("allowedValues"),
            "jira_kind":kind,"jira_multiple":multiple,"classification_evidence":evidence,
            "observed_value_shapes":[type(v).__name__ for v in
                 entry["analysis"].get("values",{}).get(source,[])[:12]],
            "current_target_id":(entry["rows"].get(source,{}).get("spec") or {}).get("field_id"),
            "candidates":[{"field_id":field_identifier(c["field"]),
                           "name":c["field"].get("label") or c["field"].get("name"),
                           "type":c["field"].get("type"),
                           "typeName":c["field"].get("typeName"),
                           "valueModel":c["field"].get("valueModel"),
                           "referenceType":c["field"].get("referenceType"),
                           "multipleSelection":c["field"].get("multipleSelection"),
                           "multipleValues":c["field"].get("multipleValues"),
                           "hidden":hidden(c["field"]),"reason":c.get("reason") or "호환"}
                          for c in candidates]}
        output=Path(wizard.state.root)/"field-mapping-diagnostic.json"
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        append_log("필드 상세",str(source)+" · Jira "+str(kind)+
                   ("/multi" if multiple else "/single")+
                   " · 후보 "+str(len(candidates))+"개 · "+str(output))
        if source.casefold()=="labels":
            append_log("Labels 진단","source ID="+str(info.get("id"))+
                       " · schema="+str(info.get("schema"))[:200])
        return report

    def open_target_dropdown(source):
        if editor[0] is not None:
            editor[0].destroy()
            editor[0] = None
        scope = current_scope()
        # Refresh CB fields when the dropdown opens, including fields
        # present in Tracker Configuration but omitted by the list endpoint.
        from migration.wizard import target_fields
        entry=wizard.plan["scopes"][scope]
        entry["target_fields"]=target_fields(wizard.cb,entry["tracker"])
        all_candidates=wizard.mapping_candidates(scope,source)
        field_diagnostics(source,all_candidates)
        candidates=[c for c in all_candidates if not hidden(c["field"])]
        choices = ["— 대상 미지정 —"]
        for candidate in candidates:
            field = candidate["field"]
            name = field.get("label") or field.get("name") or "Field"
            warning=" [확인 필요: "+str(candidate["reason"])[:55]+"]" if candidate.get("reason") else ""
            choices.append(str(name) + " · ID " + str(field_identifier(field)) + warning)
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
                def connect():
                    try:
                        wizard.manual_mapping(scope,source,fid,target_key,{})
                    except Exception as exc:
                        append_log("필드 연결 거부",str(source)+" → CB "+str(fid)+" · "+str(exc))
                        field_diagnostics(source)
                        raise
                    append_log("필드 연결 성공",str(source)+" → CB "+str(fid)+
                               " · CB type="+str(field.get("typeName") or field.get("type")))
                    refresh()
                    kind=semantic_type(field)[0]
                    if (kind in ("option","status") or
                        field.get("referenceType")=="ChoiceOptionReference"):
                        option_mapping_popup(source)
                    elif kind=="user" or field.get("referenceType")=="UserReference":
                        append_log("User 연결",str(source)+" → CB "+str(fid)+
                                   " · 같은 로그인 ID의 사용자만 자동 연결; 미확인 사용자는 수동 지정 필요")
                guarded("기존 필드 연결",connect)
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
    def map_selected_options():
        selected=table.selection()
        if not selected:
            raise ValueError("옵션을 매핑할 Jira 필드 행을 먼저 클릭하세요.")
        option_mapping_popup(selected[0])
    ttk.Button(buttons,text="선택 필드 옵션 매핑",
               command=lambda: guarded("Choice 옵션 매핑",map_selected_options)).pack(side="left",padx=8)

    def import_approved():
        from copy import deepcopy
        from types import SimpleNamespace
        from migration.simple_item_import import run as import_one
        scope = current_scope()
        key = import_issue_key.get().strip()
        if not key:
            raise ValueError("1차 Import할 Jira Issue Key를 입력하세요.")
        selected = [source for source,row in rows().items() if row.get("selected",False)]
        if "summary" not in selected:
            raise ValueError("Summary → Name 필드를 선택하세요.")
        entry=wizard.plan["scopes"][scope]
        project=entry["project"]
        if not key.startswith(project+"-"):
            raise ValueError("선택한 Jira 프로젝트의 Issue Key를 입력하세요.")
        # The checkmark selects data, but it does not assign a CB field.
        # Report all unmapped chosen fields before approval, not just the
        # first field encountered while POST payload is being prepared.
        missing=[]
        for source in selected:
            if source in ("summary","description"):
                continue
            row=entry["rows"][source]
            spec=row.get("spec") or {}
            fid=spec.get("field_id")
            if type(fid) is not int or fid<=0:
                missing.append(source+" (CB 대상 필드 미지정)")
            elif spec.get("missing_definition") or spec.get("selection_unverified"):
                # Previously saved User mappings can contain obsolete generic
                # Choice field warnings. Check actual UserReference metadata.
                jira_kind=semantic_type(entry["analysis"]["fields"][source],True)[0]
                if jira_kind=="user":
                    from clients.jira_client import segment
                    detail=wizard.cb.http.request("GET",
                        "/v3/trackers/"+segment(entry["tracker"])+"/fields/"+segment(fid))
                    if (isinstance(detail,dict) and field_identifier(detail)==fid and
                        detail.get("referenceType")=="UserReference" and
                        str(detail.get("valueModel") or "").startswith("ChoiceFieldValue") and
                        type(detail.get("multipleValues")) is bool):
                        spec.pop("missing_definition",None)
                        spec.pop("selection_unverified",None)
                        spec["user_identity_lookup"]=True
                        wizard.state.save("wizard","plan",wizard.plan)
                        append_log("User 필드 검증",source+" → CB "+str(fid)+
                                   " · 옵션 매핑 없이 계정 자동 조회")
                    else:
                        missing.append(source+" (CB UserReference 상세 계약 미확인)")
                else:
                    missing.append(source+" (매핑 타입/계약 미확인)")
        if missing:
            for problem in missing:
                append_log("선택 필드 매핑 누락",problem)
            raise ValueError("체크된 Jira 필드 중 CB 대상 매핑이 없습니다:\\n"+
                             "\\n".join(missing)+
                             "\\n데이터 가져오기 화면의 CB 필드 열에서 기존 대상을 연결하세요.")
        # Verify the reserved, unique Jira Key field BEFORE user approval.
        from migration.simple_item_import import _field
        _field(wizard.cb,entry["tracker"])
        if not messagebox.askyesno("1차 데이터 실제 Import",
                "Jira "+key+" → 기존 CB Tracker "+str(entry["tracker"])+"\\n"
                "선택 필드 "+str(len(selected))+"개를 실제 POST할 수 있습니다.\\n"
                "기존 Jira Key가 있으면 신규 생성하지 않습니다.\\n\\n"
                "Text/WikiText/Choice(옵션 매핑 완료) 외 필드는 POST 전 차단합니다. 계속할까요?"):
            return
        tabs.select(result_tab)
        append_log("1차 Import","Jira "+key+" · 필드 "+str(len(selected))+"개 · 실제 POST")
        for source in selected:
            spec=rows()[source].get("spec") or {}
            append_log("필드 선택",source+" → "+str(spec.get("target") or spec.get("field_id") or "CB 대상 미지정"))
        settings=deepcopy(wizard.config)
        settings.setdefault("jira",{})["single_issue_key"]=key
        # One selected type per write; other saved mappings remain untouched.
        limited=SimpleNamespace(plan={"scopes":{scope:deepcopy(entry)}},
                  config=settings,jira=wizard.jira,cb=wizard.cb,state=wizard.state)
        try:
            report=import_one(limited,progress=append_log)
            summarize_report(report)
            status.set("1차 Import: "+("완료" if report.get("complete") else "미완료")+
                       " · CB 조회 검증 결과를 확인하세요.")
        except Exception as exc:
            from migration.diagnostics import safe_exception_details,write_diagnostic
            details=safe_exception_details(exc)
            frames=details.get("frames") or []
            if frames:
                frame=frames[-1]
                append_log("오류 위치",str(frame.get("file"))+":"+
                           str(frame.get("line"))+" · "+str(frame.get("function")))
            try:
                append_log("상세 로그",write_diagnostic(wizard,"Import",exc))
            except Exception as log_exc:
                append_log("로그 저장 실패",type(log_exc).__name__)
            append_log("Import 실패",str(exc))
            status.set("Import 실패: "+str(exc)[:250])

    ttk.Button(buttons, text="실제 Import 실행…", command=lambda: guarded("Import", import_approved)).pack(side="right", padx=6)
    root.mainloop()
