import os
os.environ['QT_QPA_PLATFORM']='windows'
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from eryou_panel.config import Config
from eryou_panel.gui.theme import apply_theme
from eryou_panel.gui.window import FloatingPanel
app=QApplication([]); apply_theme(app)
w=FloatingPanel(Config(), compact=False); w.show(); QTest.qWait(900)
w._select_project('ok-ww-enhanced',force=True); QTest.qWait(300)
r=w.controller.runtimes['ok-ww-enhanced']; print('INITIALIZED',r.initialized,'MODE',w.controller.adapters['ok-ww-enhanced'].mode_text(),'STATUS',w.overview.status.text() if hasattr(w.overview.status,'text') else '?')
print('STATUS_TEXT',w.overview.status.findChildren(type(w.overview.status))[:0])
print('SIDE',w.side_install.text())
w.tabs.setCurrentIndex(0); w.grab().save('_ww_init.png')
w._show_global('update'); QTest.qWait(300); print('WW_ROW_STATUS',w.install_page.rows['ok-ww-enhanced'][1].text(),w.install_page.rows['ok-ww-enhanced'][2].text()); w.grab().save('_ww_update.png')
w.hide(); w.deleteLater(); app.processEvents(); app.quit(); print('OK')
