import os
os.environ['QT_QPA_PLATFORM']='windows'
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from eryou_panel.config import Config
from eryou_panel.gui.theme import apply_theme
from eryou_panel.gui.window import FloatingPanel
app=QApplication([]); apply_theme(app)
w=FloatingPanel(Config(), compact=False); w.show(); QTest.qWait(1000)
w._select_project('ok-nte',force=True); QTest.qWait(300); w.grab().save('_ui_new.png')
w._show_global('update'); QTest.qWait(300); w.grab().save('_ui_update.png')
w._show_global('display'); QTest.qWait(300); w.grab().save('_ui_display.png')
w._show_project_view(); w._set_bureau('android'); QTest.qWait(300); w.grab().save('_ui_android.png')
print('TABS',[w.tabs.tabText(i) for i in range(w.tabs.count())],'BUREAU',w._bureau)
w.hide(); w.deleteLater(); app.processEvents(); app.quit(); print('OK')
