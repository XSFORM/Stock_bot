from aiogram.fsm.state import State, StatesGroup


class ClientAdd(StatesGroup):
    waiting_name = State()


class ProductAdd(StatesGroup):
    waiting_brand = State()
    waiting_model = State()
    waiting_name = State()
    waiting_price = State()
