import io

import factory
from factory.django import DjangoModelFactory

from artifact.models import BlobObject, guess_content_type
from user.factories import UserFactory

from .models import Resource, ResourceVersion


class ResourceFactory(DjangoModelFactory[Resource]):
    class Meta:
        model = Resource

    created_by = factory.SubFactory(UserFactory)


class ResourceVersionFactory(DjangoModelFactory[ResourceVersion]):
    """既定の保存先に中身を書いて作る（テストでは保存先を一時ディレクトリにする）"""

    class Meta:
        model = ResourceVersion

    class Params:
        content = b"data"

    resource = factory.SubFactory(ResourceFactory)
    number = factory.Sequence(lambda n: n + 1)
    name = factory.Sequence(lambda n: f"file{n}.md")
    content_type = factory.LazyAttribute(lambda o: guess_content_type(o.name))
    blob = factory.LazyAttribute(lambda o: BlobObject.put(io.BytesIO(o.content)))
    created_by = factory.SelfAttribute("resource.created_by")
