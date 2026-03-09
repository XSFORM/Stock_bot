def search_products_for_price(q: str, limit: int = 30, mode: str = "SIMPLE"):
    connection = sqlite3.connect('your_database.db')
    cursor = connection.cursor()
    query = f"SELECT * FROM products WHERE (brand LIKE ? OR model LIKE ? OR name LIKE ?) AND archived = 0 LIMIT ?"
    cursor.execute(query, ('%'+q+'%', '%'+q+'%', '%'+q+'%', limit))
    results = cursor.fetchall()
    products = [_apply_price_mode(product, mode) for product in results]
    connection.close()
    return products

def set_price_token_mode(mode: str):
    # Existing logic for setting the price token mode
    pass
