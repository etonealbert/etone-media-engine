"""
FSM state definitions for the /generate video wizard.
"""

from aiogram.fsm.state import State, StatesGroup


class GenerateStates(StatesGroup):
    """States for the multi-step video generation wizard."""

    select_avatar = State()  # Step 1: Choose avatar (optional)
    select_location = State()  # Step 2: Choose location (optional)
    select_product = State()  # Step 3: Choose product (optional)
    enter_task = State()  # Step 4: Amazon link or task description (optional)
    select_length = State()  # Step 5: Target video length (optional, default 15s)
    enter_context = State()  # Step 6: Creative context / wishes (optional)
    select_mode = State()  # Step 7: Select generation mode (HITL or Autonomous)
    confirm = State()  # Step 8: Review summary and confirm launch
    wait_human_kling_output = State()  # Waiting for Albert manual Kling result
