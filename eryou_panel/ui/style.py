"""Textual 样式表：暖白 + 香槟金，圆角与半透明分层（终端内的“毛玻璃”近似）。"""

STYLE = """
Screen {
    background: #f3f0e9;
    color: #35332d;
}

Header {
    background: #fbf9f4;
    color: #2b2a26;
}

Header .header--title {
    color: #8a6d3b;
    text-style: bold;
}

Header .header--subtitle {
    color: #8b8477;
}

Footer {
    background: #fbf9f4;
    color: #8b8477;
}

Footer .footer--key {
    background: #f1e8d5;
    color: #8a6d3b;
    text-style: bold;
}

#body {
    height: 1fr;
    padding: 0 1;
}

#sidebar {
    width: 38;
    min-width: 32;
    background: #fbf8f2 92%;
    border-right: solid #ece5d7;
    padding: 0;
}

#side_title {
    height: 3;
    padding: 1 1 0 2;
    color: #8a6d3b;
    text-style: bold;
}

#projects {
    height: 1fr;
    background: transparent;
    padding: 0;
}

ProjectItem {
    height: auto;
    padding: 0 1;
    margin: 0 1 0 1;
    background: #ffffff 88%;
    border: round #ece5d7;
}

ProjectItem.-highlight {
    background: #fdf5e3 96%;
    border: round #c9a227;
}

ProjectItem.missing {
    opacity: 72%;
}

#side_hint {
    height: auto;
    padding: 0 2 1 2;
    color: #a29a8c;
}

#content {
    width: 1fr;
    background: #fffdf8 94%;
    border: round #ece5d7;
    margin: 1 0 0 1;
    padding: 0 1;
}

#proj_header {
    height: auto;
    padding: 1 1 0 1;
}

#tabs {
    height: 1fr;
    margin-top: 0;
}

Tabs {
    height: 2;
    background: transparent;
    padding: 0 1;
}

Tab {
    color: #a29a8c;
    background: transparent;
    height: 1;
    padding: 0 2;
}

Tab.-active {
    color: #8a6d3b;
    background: #f5ecd8;
    text-style: bold;
}

Tabs .underline--bar {
    color: #c9a227;
    background: #c9a227;
}

Tabs:focus .underline--bar {
    color: #c9a227;
    background: #c9a227;
}

Tabs #tabs-list > .-active {
    color: #8a6d3b;
    background: #f7eeda;
    text-style: bold;
}

Tab:hover {
    color: #8a6d3b;
}

TabPane {
    height: 1fr;
    padding: 0;
    background: transparent;
}

TabbedContent ContentSwitcher {
    height: 1fr;
    background: transparent;
}

#overview_scroll {
    height: 1fr;
}

#overview {
    height: auto;
    padding: 1 1;
}

#task_hint {
    height: auto;
    padding: 0 1 1 1;
    color: #8b8477;
}

DataTable {
    height: 1fr;
    background: #fffefa 90%;
    border: round #efe8da;
}

DataTable > .datatable--header {
    background: #f5efe2;
    color: #8a6d3b;
    text-style: bold;
}

DataTable > .datatable--cursor {
    background: #c9a227 28%;
    color: #2b2a26;
}

DataTable > .datatable--hover {
    background: #fbf3e1;
}

#log_bar {
    height: 3;
    padding: 0 0 1 0;
}

#log_select {
    width: 46;
}

#log_filter {
    width: 1fr;
}

#log_follow {
    width: auto;
    margin: 0 0 0 1;
}

RichLog {
    height: 1fr;
    background: #fdfcf8;
    border: round #e8e1d3;
    padding: 0 1;
    color: #3f3c36;
}

#cfg_bar {
    height: auto;
    padding: 0 0 1 0;
}

#cfg_bar Button {
    margin-right: 1;
}

#config_table {
    height: 45%;
}

#config_preview {
    height: 1fr;
    background: #fdfcf8;
    border: round #e8e1d3;
    padding: 0 1;
    color: #6f6a5f;
}

/* ---- 表单 ---- */
Input {
    background: #ffffff 92%;
    border: round #e4dbca;
    color: #2b2a26;
}

Input:focus {
    border: round #c9a227;
}

Select {
    background: #ffffff 92%;
    border: round #e4dbca;
    color: #2b2a26;
}

Select:focus {
    border: round #c9a227;
}

Button {
    background: #fffdf8;
    border: round #ded4c1;
    color: #4a463d;
    min-width: 12;
}

Button:hover {
    background: #fdf5e3;
    border: round #c9a227;
}

Button.-primary {
    background: #c9a227;
    border: round #c9a227;
    color: #fffdf8;
    text-style: bold;
}

Button.-primary:hover {
    background: #b08d57;
    border: round #b08d57;
}

Switch > .switch--slider {
    color: #c9a227;
}

/* ---- 弹窗：居中的圆角玻璃卡片 ---- */
ModalScreen {
    align: center middle;
    background: #2b2a26 32%;
}

#dialog {
    width: 86;
    height: auto;
    max-height: 90%;
    padding: 1 2;
    background: #fffdf7 98%;
    border: round #c9a227;
}

#dialog_title {
    text-style: bold;
    color: #8a6d3b;
    padding-bottom: 1;
}

.dialog_label {
    color: #8b8477;
    padding-top: 1;
}

#dialog_input {
    margin-bottom: 1;
}

#dialog_buttons {
    height: auto;
    align-horizontal: right;
    padding-top: 1;
}

#dialog_buttons Button {
    margin-left: 1;
}

#dialog_hint {
    color: #a29a8c;
    padding-top: 1;
}

#help_body {
    height: 1fr;
}

#command_input {
    margin-top: 1;
}

#install_path {
    color: #6f6a5f;
    padding-bottom: 1;
}

#install_status {
    color: #8a6d3b;
    padding: 1 0;
}

#install_log {
    height: 12;
    background: #fdfcf8;
    border: round #e8e1d3;
    padding: 0 1;
    color: #3f3c36;
}

ProgressBar {
    width: 1fr;
}

Bar > .bar--bar {
    color: #c9a227;
    background: #f1e8d5;
}

Bar > .bar--complete {
    color: #8a6d3b;
    background: #f1e8d5;
}

PercentageStatus {
    color: #8a6d3b;
}

#scan_log {
    height: 14;
    border: round #e8e1d3;
    background: #fdfcf8;
}

.error_text {
    color: #b4493f;
}

.muted {
    color: #a29a8c;
}
"""
