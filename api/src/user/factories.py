import factory
from factory.django import DjangoModelFactory

from .models import User


class UserFactory(DjangoModelFactory[User]):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.co.jp")
    display_name = factory.Sequence(lambda n: f"社員{n}")
