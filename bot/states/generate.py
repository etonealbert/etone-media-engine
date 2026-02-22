"""
FSM state definitions for the /generate video wizard.
"""

from aiogram.fsm.state import State, StatesGroup


class GenerateStates(StatesGroup):
    """States for the multi-step video generation wizard."""

    select_avatar = State()  # Step 1: Choose avatar from asset index
    select_location = State()  # Step 2: Choose location
    select_product = State()  # Step 3: Choose product
    enter_task = State()  # Step 4: Amazon link or task description
    select_length = State()  # Step 5: Target video length
    confirm = State()  # Step 6: Review summary and confirm launch
