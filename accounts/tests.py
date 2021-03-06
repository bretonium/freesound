#
# Freesound is (c) MUSIC TECHNOLOGY GROUP, UNIVERSITAT POMPEU FABRA
#
# Freesound is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# Freesound is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Authors:
#     See AUTHORS file.
#

from django.test import TestCase
from django.test.utils import override_settings
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.core.files.uploadedfile import InMemoryUploadedFile, SimpleUploadedFile
from django.conf import settings
from accounts.forms import RecaptchaForm
from accounts.models import Profile
from accounts.views import handle_uploaded_image
from accounts.admin import delete_active_user, delete_active_user_preserve_sounds
from sounds.models import License, Sound, Pack, DeletedSound
from tags.models import TaggedItem
from utils.filesystem import File
from tags.models import Tag
from comments.models import Comment
from forum.models import Thread, Post, Forum
import accounts.models
import mock
import os
import tempfile
import shutil


class OldUserLinksRedirect(TestCase):
    
    fixtures = ['users']
    
    def setUp(self):
        self.user = User.objects.all()[0]
        
    def test_old_user_link_redirect_ok(self):
        # 301 permanent redirect, result exists
        resp = self.client.get(reverse('old-account-page'), data={'id': self.user.id})
        self.assertEqual(resp.status_code, 301)
        
    def test_old_user_link_redirect_not_exists_id(self):
        # 404 id does not exist (user with id 999 does not exist in fixture)
        resp = self.client.get(reverse('old-account-page'), data={'id': 999}, follow=True)
        self.assertEqual(resp.status_code, 404)
        
    def test_old_user_link_redirect_invalid_id(self):
        # 404 invalid id
        resp = self.client.get(reverse('old-account-page'), data={'id': 'invalid_id'}, follow=True)
        self.assertEqual(resp.status_code, 404)


class UserRegistrationAndActivation(TestCase):

    fixtures = ['users']

    def test_user_registration(self):
        RecaptchaForm.validate_captcha = lambda x: True  # Monkeypatch recaptcha validation so the form validates
        resp = self.client.post("/home/register/", {'username': 'testuser',
                                                    'first_name': 'test_first_name',
                                                    'last_name': 'test_last_name',
                                                    'email1': 'email@example.com',
                                                    'email2': 'email@example.com',
                                                    'password1': 'testpass',
                                                    'password2': 'testpass',
                                                    'newsletter': '1',
                                                    'accepted_tos': '1',
                                                    'recaptcha_challenge_field': 'a',
                                                    'recaptcha_response_field': 'a'})

        self.assertEqual(resp.status_code, 200)

        u = User.objects.get(username='testuser')
        self.assertEqual(u.profile.wants_newsletter, True)  # Check profile parameters are set correctly
        self.assertEqual(u.profile.accepted_tos, True)

        u.is_active = True  # Set user active and check it can login
        u.save()
        self.assertEqual(self.client.login(username='testuser', password='testpass'), True)

    def test_user_save(self):
        u = User.objects.create_user("testuser2", password="testpass")
        self.assertEqual(Profile.objects.filter(user=u).exists(), True)
        u.save()  # Check saving user again (with existing profile) does not fail

    def test_user_activation(self):
        user = User.objects.get(username="User6Inactive")  # Inactive user in fixture

        # Test calling accounts-activate with wrong hash, user should not be activated
        bad_hash = '4dad3dft'
        resp = self.client.get(reverse('accounts-activate', args=[user.username, bad_hash]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['decode_error'], True)
        self.assertEqual(User.objects.get(username="User6Inactive").is_active, False)

        # Test calling accounts-activate with good hash, user should be activated
        from utils.encryption import create_hash
        good_hash = create_hash(user.id)
        resp = self.client.get(reverse('accounts-activate', args=[user.username, good_hash]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['all_ok'], True)
        self.assertEqual(User.objects.get(username="User6Inactive").is_active, True)

        # Test calling accounts-activate for a user that does not exist
        resp = self.client.get(reverse('accounts-activate', args=["noone", hash]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['user_does_not_exist'], True)


class ProfileGetUserTags(TestCase):

    fixtures = ['sounds_with_tags']

    def test_user_tagcloud_solr(self):
        user = User.objects.get(username="Anton")
        mock_solr = mock.Mock()
        conf = {
            'select.return_value': {
                'facet_counts': {
                    'facet_ranges': {},
                    'facet_fields': {'tag': ['conversation', 1, 'dutch', 1, 'glas', 1, 'glass', 1, 'instrument', 2,
                                             'laughter', 1, 'sine-like', 1, 'struck', 1, 'tone', 1, 'water', 1]},
                    'facet_dates': {},
                    'facet_queries': {}
                },
                'responseHeader': {
                    'status': 0,
                    'QTime': 4,
                    'params': {'fq': 'username:\"Anton\"', 'facet.field': 'tag', 'f.tag.facet.limit': '10',
                               'facet': 'true', 'wt': 'json', 'f.tag.facet.mincount': '1', 'fl': 'id', 'qt': 'dismax'}
                },
                'response': {'start': 0, 'numFound': 48, 'docs': []}
            }
        }
        mock_solr.return_value.configure_mock(**conf)
        accounts.models.Solr = mock_solr
        tag_names = [item["name"] for item in list(user.profile.get_user_tags(use_solr=True))]
        used_tag_names = list(set([item.tag.name for item in TaggedItem.objects.filter(user=user)]))
        non_used_tag_names = list(set([item.tag.name for item in TaggedItem.objects.exclude(user=user)]))

        # Test that tags retrieved with get_user_tags are those found in db
        self.assertEqual(len(set(tag_names).intersection(used_tag_names)), len(tag_names))
        self.assertEqual(len(set(tag_names).intersection(non_used_tag_names)), 0)

        # Test solr not available return False
        conf = {'select.side_effect': Exception}
        mock_solr.return_value.configure_mock(**conf)
        self.assertEqual(user.profile.get_user_tags(use_solr=True), False)

    def test_user_tagcloud_db(self):
        user = User.objects.get(username="Anton")
        tag_names = [item["name"] for item in list(user.profile.get_user_tags(use_solr=False))]
        used_tag_names = list(set([item.tag.name for item in TaggedItem.objects.filter(user=user)]))
        non_used_tag_names = list(set([item.tag.name for item in TaggedItem.objects.exclude(user=user)]))

        # Test that tags retrieved with get_user_tags are those found in db
        self.assertEqual(len(set(tag_names).intersection(used_tag_names)), len(tag_names))
        self.assertEqual(len(set(tag_names).intersection(non_used_tag_names)), 0)


class UserEditProfile(TestCase):

    @override_settings(AVATARS_PATH=tempfile.mkdtemp())
    def test_handle_uploaded_image(self):
        user = User.objects.create_user("testuser", password="testpass")
        f = InMemoryUploadedFile(open(settings.MEDIA_ROOT + '/images/70x70_avatar.png'), None, None, None, None, None)
        handle_uploaded_image(user.profile, f)

        # Test that avatar files were created
        self.assertEqual(os.path.exists(user.profile.locations("avatar.S.path")), True)
        self.assertEqual(os.path.exists(user.profile.locations("avatar.M.path")), True)
        self.assertEqual(os.path.exists(user.profile.locations("avatar.L.path")), True)

        # Delete tmp directory
        shutil.rmtree(settings.AVATARS_PATH)

    def test_edit_user_profile(self):
        User.objects.create_user("testuser", password="testpass")
        self.client.login(username='testuser', password='testpass')
        self.client.post("/home/edit/", {
            'profile-home_page': 'http://www.example.com/',
            'profile-wants_newsletter': True,
            'profile-enabled_stream_emails': True,
            'profile-about': 'About test text',
            'profile-signature': 'Signature test text',
            'profile-not_shown_in_online_users_list': True,
        })

        user = User.objects.select_related('profile').get(username="testuser")
        self.assertEqual(user.profile.home_page, 'http://www.example.com/')
        self.assertEqual(user.profile.wants_newsletter, True)
        self.assertEqual(user.profile.enabled_stream_emails, True)
        self.assertEqual(user.profile.about, 'About test text')
        self.assertEqual(user.profile.signature, 'Signature test text')
        self.assertEqual(user.profile.not_shown_in_online_users_list, True)

    @override_settings(AVATARS_PATH=tempfile.mkdtemp())
    def test_edit_user_avatar(self):
        User.objects.create_user("testuser", password="testpass")
        self.client.login(username='testuser', password='testpass')
        self.client.post("/home/edit/", {
            'image-file': open(settings.MEDIA_ROOT + '/images/70x70_avatar.png'),
            'image-remove': False,
        })

        user = User.objects.select_related('profile').get(username="testuser")
        self.assertEqual(user.profile.has_avatar, True)
        self.assertEqual(os.path.exists(user.profile.locations("avatar.S.path")), True)
        self.assertEqual(os.path.exists(user.profile.locations("avatar.M.path")), True)
        self.assertEqual(os.path.exists(user.profile.locations("avatar.L.path")), True)

        self.client.post("/home/edit/", {
            'image-file': '',
            'image-remove': True,
        })
        user = User.objects.select_related('profile').get(username="testuser")
        self.assertEqual(user.profile.has_avatar, False)

        # Delete tmp directory
        shutil.rmtree(settings.AVATARS_PATH)


class UserUploadAndDescribeSounds(TestCase):

    fixtures = ['initial_data']

    @override_settings(UPLOADS_PATH=tempfile.mkdtemp())
    def test_handle_uploaded_file_html(self):
        # TODO: test html5 file uploads when we change uploader
        user = User.objects.create_user("testuser", password="testpass")
        self.client.login(username='testuser', password='testpass')

        # Test successful file upload
        filename = "file.wav"
        f = SimpleUploadedFile(filename, "file_content")
        resp = self.client.post("/home/upload/html/", {'file': f})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(os.path.exists(settings.UPLOADS_PATH + '/%i/%s' % (user.id, filename)), True)

        # Test file upload that should fail
        filename = "file.xyz"
        f = SimpleUploadedFile(filename, "file_content")
        resp = self.client.post("/home/upload/html/", {'file': f})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(os.path.exists(settings.UPLOADS_PATH + '/%i/%s' % (user.id, filename)), False)

        # Delete tmp directory
        shutil.rmtree(settings.UPLOADS_PATH)

    @override_settings(UPLOADS_PATH=tempfile.mkdtemp())
    def test_select_uploaded_files_to_describe(self):
        # Create audio files
        filenames = ['file1.wav', 'file2.wav', 'file3.wav']
        user = User.objects.create_user("testuser", password="testpass")
        self.client.login(username='testuser', password='testpass')
        user_upload_path = settings.UPLOADS_PATH + '/%i/' % user.id
        os.mkdir(user_upload_path)
        for filename in filenames:
            f = open(user_upload_path + filename, 'a')
            f.write(os.urandom(1024))  # Add random content to the file to avoid equal md5
            f.close()

        # Check that files are displayed in the template
        resp = self.client.get('/home/describe/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['file_structure'].children), len(filenames))

        # Selecting one file redirects to /home/describe/sounds/
        resp = self.client.post('/home/describe/', {
            'describe': [u'Describe selected files'],
            'files': [u'file1'],
        })
        self.assertRedirects(resp, '/home/describe/sounds/')

        # Selecting multiple file redirects to /home/describe/license/
        resp = self.client.post('/home/describe/', {
            'describe': [u'Describe selected files'],
            'files': [u'file1', u'file0'],
        })
        self.assertRedirects(resp, '/home/describe/license/')

        # Selecting files to delete, redirecte to delete confirmation
        filenames_to_delete = [u'file1', u'file0']
        resp = self.client.post('/home/describe/', {
            'delete': [u'Delete selected files'],
            'files': filenames_to_delete,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['filenames']), len(filenames_to_delete))

        # Selecting confirmation of files to delete
        resp = self.client.post('/home/describe/', {
            'delete_confirm': [u'delete_confirm'],
            'files': filenames_to_delete,
        })
        self.assertRedirects(resp, '/home/describe/')
        self.assertEqual(len(os.listdir(user_upload_path)), len(filenames) - len(filenames_to_delete))

        # Delete tmp directory
        shutil.rmtree(settings.UPLOADS_PATH)

    @override_settings(UPLOADS_PATH=tempfile.mkdtemp())
    def test_describe_selected_files(self):
        # Create audio files
        filenames = ['file1.wav', 'file2.wav']
        user = User.objects.create_user("testuser", password="testpass")
        self.client.login(username='testuser', password='testpass')
        user_upload_path = settings.UPLOADS_PATH + '/%i/' % user.id
        os.mkdir(user_upload_path)
        for filename in filenames:
            f = open(user_upload_path + filename, 'a')
            f.write(os.urandom(1024))  # Add random content to the file to avoid equal md5
            f.close()

        # Set license and pack data in session
        session = self.client.session
        session['describe_license'] = License.objects.all()[0]
        session['describe_pack'] = False
        session['describe_sounds'] = [File(1, filenames[0], user_upload_path + filenames[0], False),
                                      File(2, filenames[1], user_upload_path + filenames[1], False)]
        session.save()

        # Post description information
        resp = self.client.post('/home/describe/sounds/', {
            'submit': [u'Submit and continue'],
            '0-lat': [u'46.31658418182218'],
            '0-lon': [u'3.515625'],
            '0-zoom': [u'16'],
            '0-tags': [u'testtag1 testtag2 testtag3'],
            '0-pack': [u''],
            '0-license': [u'3'],
            '0-description': [u'a test description for the sound file'],
            '0-new_pack': [u''],
            '0-name': [u'%s' % filenames[0]],
            '1-license': [u'3'],
            '1-description': [u'another test description'],
            '1-lat': [u''],
            '1-pack': [u''],
            '1-lon': [u''],
            '1-name': [u'%s' % filenames[1]],
            '1-new_pack': [u'Name of a new pack'],
            '1-zoom': [u''],
            '1-tags': [u'testtag1 testtag4 testtag5'],
        })

        # Check that post redirected to first describe page with confirmation message on sounds described
        self.assertRedirects(resp, '/home/describe/')
        self.assertEqual('You have described all the selected files' in resp.cookies['messages'].value, True)

        # Check that sounds have been created along with related tags, geotags and packs
        self.assertEqual(user.sounds.all().count(), 2)
        self.assertEqual(user.pack_set.filter(name='Name of a new pack').exists(), True)
        self.assertEqual(Tag.objects.filter(name__contains="testtag").count(), 5)
        self.assertNotEqual(user.sounds.get(original_filename=filenames[0]).geotag, None)


class UserDelete(TestCase):

    fixtures = ['sounds']

    def create_user_and_content(self):
        user = User.objects.create_user("testuser", password="testpass")
        # Create comments
        target_sound = Sound.objects.all()[0]
        for i in range(0, 3):
            comment = Comment(comment="Comment %i" % i, user=user)
            target_sound.add_comment(comment)
        # Create threads and posts
        thread = Thread.objects.create(author=user, title="Test thread", forum=Forum.objects.create(name="Test forum"))
        for i in range(0, 3):
            Post.objects.create(author=user, thread=thread, body="Post %i body" % i)
        # Create deleted sounds
        for i in range(0, 3):
            DeletedSound.objects.create(user=user, sound_id=i)  # Using fake sound id here
        # Create sounds and packs
        pack = Pack.objects.create(user=user, name="Test pack")
        for i in range(0, 3):
            Sound.objects.create(user=user,
                                 original_filename="Test sound %i" % i,
                                 pack=pack,
                                 license=License.objects.all()[0],
                                 md5="fakemd5%i" % i)

        return user

    def test_user_delete(self):
        # This should delete all user related objects
        user = self.create_user_and_content()
        user.delete()
        self.assertEqual(User.objects.filter(id=user.id).exists(), False)
        self.assertEqual(Comment.objects.filter(user__id=user.id).exists(), False)
        self.assertEqual(Thread.objects.filter(author__id=user.id).exists(), False)
        self.assertEqual(Post.objects.filter(author__id=user.id).exists(), False)
        self.assertEqual(DeletedSound.objects.filter(user__id=user.id).exists(), False)
        self.assertEqual(Pack.objects.filter(user__id=user.id).exists(), False)
        self.assertEqual(Sound.objects.filter(user__id=user.id).exists(), False)

    @override_settings(DELETED_USER_ID=0)  # 0 = deleted_user id in fixture
    def test_user_delete_active_user(self):
        # This should delete all user related objects except for Comments, Threads, Posts and DeletedSounds
        user = self.create_user_and_content()
        delete_active_user(None, None, User.objects.filter(id=user.id))
        self.assertEqual(User.objects.filter(id=user.id).exists(), False)
        self.assertEqual(Comment.objects.filter(user__id=settings.DELETED_USER_ID).exists(), True)
        self.assertEqual(Thread.objects.filter(author__id=settings.DELETED_USER_ID).exists(), True)
        self.assertEqual(Post.objects.filter(author__id=settings.DELETED_USER_ID).exists(), True)
        self.assertEqual(DeletedSound.objects.filter(user__id=settings.DELETED_USER_ID).exists(), True)
        self.assertEqual(Pack.objects.filter(user__id=user.id).exists(), False)
        self.assertEqual(Sound.objects.filter(user__id=user.id).exists(), False)

    @override_settings(DELETED_USER_ID=0)  # 0 = deleted_user id in fixture
    def test_user_delete_active_user_preserve_sounds(self):
        # This should delete all user related objects except for Comments, Threads, Posts, DeletedSounds, Sound and Packs
        user = self.create_user_and_content()
        delete_active_user_preserve_sounds(None, None, User.objects.filter(id=user.id))
        self.assertEqual(User.objects.filter(id=user.id).exists(), False)
        self.assertEqual(Comment.objects.filter(user__id=settings.DELETED_USER_ID).exists(), True)
        self.assertEqual(Thread.objects.filter(author__id=settings.DELETED_USER_ID).exists(), True)
        self.assertEqual(Post.objects.filter(author__id=settings.DELETED_USER_ID).exists(), True)
        self.assertEqual(DeletedSound.objects.filter(user__id=settings.DELETED_USER_ID).exists(), True)
        self.assertEqual(Pack.objects.filter(user__id=settings.DELETED_USER_ID).exists(), True)
        self.assertEqual(Sound.objects.filter(user__id=settings.DELETED_USER_ID).exists(), True)
