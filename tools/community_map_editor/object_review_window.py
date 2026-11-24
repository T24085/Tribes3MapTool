"""Build the profile-configured object review and selection details in Tk."""

import tkinter as tk
from tkinter import ttk

from object_catalog import SUPPORT, source_fields


def attach_scrollbars(parent, table):
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)
    table.grid(row=0, column=0, sticky="nsew")
    vertical = ttk.Scrollbar(parent, command=table.yview)
    horizontal = ttk.Scrollbar(parent, command=table.xview, orient="horizontal")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)


class ObjectReview:
    def __init__(self, parent, catalog):
        self.catalog = catalog
        self.query = tk.StringVar(parent)
        self.review_only = tk.BooleanVar(parent)
        self.feature = tk.StringVar(parent, value="All features")
        self.labels = {row["label"]: key for key, row in catalog.features.items()}
        tabs = ttk.Notebook(parent)
        tabs.pack(fill="both", expand=True, padx=10, pady=8)
        frames = {"all": ttk.Frame(tabs, padding=8), "features": ttk.Frame(tabs, padding=8)}
        for tab in catalog.layout["tabs"]:
            tabs.add(frames[tab["id"]], text=tab["label"])
        self.filters = ttk.Frame(frames["all"])
        self.filters.pack(fill="x", pady=(0, 8))
        pane = ttk.Panedwindow(frames["all"], orient="horizontal")
        pane.pack(fill="both", expand=True)
        list_body = ttk.Frame(pane)
        pane.add(list_body, weight=3)
        self.columns = [item["id"] for item in catalog.layout["columns"]]
        self.table = ttk.Treeview(list_body, show="headings", columns=self.columns, selectmode="browse")
        for column in catalog.layout["columns"]:
            key = column["id"]
            self.table.heading(key, text=column["label"])
            self.table.column(key, width=column["width"], minwidth=65, stretch=key == "reason")
        self.table.tag_configure("blocked", foreground="#a41d2b")
        self.table.tag_configure("review", foreground="#855000")
        attach_scrollbars(list_body, self.table)

        inspector = ttk.Frame(pane, padding=(10, 0, 0, 0))
        pane.add(inspector, weight=1)
        self.title = ttk.Label(inspector, text="Object details", font="TkHeadingFont", wraplength=340)
        self.title.pack(fill="x")
        self.issues = ttk.Label(inspector, wraplength=320)
        self.issues.pack(fill="x", pady=(4, 6))
        self.connections = ttk.Label(inspector, wraplength=320)
        self.connections.pack(fill="x", pady=(0, 5))
        props = ttk.Frame(inspector)
        props.pack(fill="both", expand=True)
        self.fields = ttk.Treeview(props, show="headings", columns=("property", "value", "status"), height=9)
        for key, title, width in (("property", "Property", 110), ("value", "Value", 125), ("status", "Origin", 82)):
            self.fields.heading(key, text=title)
            self.fields.column(key, width=width, minwidth=65)
        self.fields.tag_configure("Needs review", foreground="#a41d2b")
        attach_scrollbars(props, self.fields)
        self.summary = ttk.Label(frames["all"], anchor="w")
        self.summary.pack(fill="x", pady=(6, 0))
        self.show_details(None, None, {})

        matrix = ttk.Treeview(frames["features"], show="headings", columns=("feature", "draft", "host", "details"))
        for key, title, width in (("feature", "Source feature", 258), ("draft", "Unreal draft", 100),
                                  ("host", "Direct Host", 100), ("details", "Coverage", 520)):
            matrix.heading(key, text=title)
            matrix.column(key, width=width, minwidth=65, stretch=key == "details")
        for feature in catalog.features.values():
            values = (feature["label"], SUPPORT[feature["draft"]], SUPPORT[feature["host"]], feature["detail"])
            matrix.insert("", "end", values=values)
        attach_scrollbars(frames["features"], matrix)

    def register_filters(self, change):
        available = self.catalog.layout["filters"]
        if "query" in available:
            ttk.Label(self.filters, text="Search objects").pack(side="left", padx=(0, 5))
            field = ttk.Entry(self.filters, textvariable=self.query, width=24)
            field.pack(side="left", padx=(0, 12))
            self.query.trace_add("write", lambda *_: change())
        if "feature" in available:
            feature = ttk.Combobox(self.filters, textvariable=self.feature,
                                   values=("All features", *self.labels), state="readonly", width=26)
            feature.pack(side="left", padx=(0, 12))
            feature.bind("<<ComboboxSelected>>", lambda _: change())
        if "review" in available:
            ttk.Checkbutton(self.filters, text="Needs review only", variable=self.review_only, command=change).pack(
                side="left")

    def show_details(self, row, assessment, links):
        self.fields.delete(*self.fields.get_children())
        if row is None:
            self.title.configure(text="Object details")
            self.issues.configure(text="Select a source object to inspect its properties and links.")
            self.connections.configure(text="")
            return
        self.title.configure(text=row["name"] or row["source_class"])
        self.issues.configure(text=f"{assessment['status']} · {assessment['reason']}")
        self.connections.configure(text="\n".join(
            f"{label} · {links[key]}" for key, label in self.catalog.relationships.items() if key in links))
        for field in source_fields(row, self.catalog):
            value = f"{field['value']} {field['unit']}".strip()
            if not value:
                value = field["problem"]
            self.fields.insert("", "end", values=(field["label"], value, field["status"]), tags=(field["status"],))
