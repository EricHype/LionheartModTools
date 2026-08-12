"""Small Qt widgets shared by the editors.

One entry so far, and it exists because of a real, silent data loss: a stray mouse wheel
over a combo box in the dialogue editor's reply list retargeted a reply from
`1 Conversation Start` to `10 Transformation` -- one notch down the list -- and saved it.
Nothing on screen said so. In-game that would have handed the player the quest's payoff
scene for *declining* the quest.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox


class NoScrollComboBox(QComboBox):
    """A combo box that ignores the wheel unless the user has deliberately focused it.

    Qt's default is to treat a wheel event over an unfocused combo box as a value change.
    Inside a scroll area that is a trap: the user means to scroll the list, the pointer
    happens to sit over a combo, and a field changes silently. Passing the event up lets
    the scroll area do what the user meant.

    Both editors put editable combos in scrolling docks, so both want this.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Without this the box takes focus from a click-through and the guard below
        # would then let the wheel edit it after all.
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
