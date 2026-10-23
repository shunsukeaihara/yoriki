import io

import factory
from factory.django import DjangoModelFactory

from .models import Artifact, ArtifactKind, BlobObject, guess_content_type


class ArtifactFactory(DjangoModelFactory[Artifact]):
    """既定の保存先に中身を書いて作る（テストでは保存先を一時ディレクトリにする）"""

    class Meta:
        model = Artifact

    class Params:
        content = b"data"

    owner = factory.SelfAttribute("conversation.owner")
    conversation = factory.SubFactory("conversation.factories.ConversationFactory")
    kind = ArtifactKind.ATTACHMENT
    name = factory.Sequence(lambda n: f"file{n}.txt")
    content_type = factory.LazyAttribute(lambda o: guess_content_type(o.name))
    blob = factory.LazyAttribute(lambda o: BlobObject.put(io.BytesIO(o.content)))
