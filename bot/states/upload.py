"""
FSM state definitions for the file upload flow.
"""

from aiogram.fsm.state import State, StatesGroup


class UploadStates(StatesGroup):
    """States for the media upload categorization wizard."""

    waiting_category = State()  # File received, waiting for category pick
    waiting_folder_choice = State()  # Subfolder category: new or existing?
    waiting_folder_name = State()  # New folder: enter folder name
    waiting_folder_select = State()  # Existing folder: pick from list
