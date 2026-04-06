import random
from datetime import date


def random_date(year: int) -> date:
    start_of_year = date(year, 1, 1)
    end_of_year = date(year, 12, 31)
    return date.fromordinal(
        random.randint(start_of_year.toordinal(), end_of_year.toordinal())
    )
