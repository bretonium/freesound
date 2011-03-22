from accounts import models as auth_models
from django.db.models.signals import post_syncdb
from django.contrib.auth.models import User
from accounts.models import Profile
import logging

logger = logging.getLogger("web")

def create_super_profile(**kwargs):
    for user in User.objects.filter(profile=None): # create profiles for all users that don't have profiles yet
        logger.info("\tcreating profile for super user: %s",  user)
        profile = Profile(user=user)
        profile.save()
        
post_syncdb.connect(create_super_profile, sender=auth_models)
