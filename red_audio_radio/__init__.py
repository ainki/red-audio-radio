from .red_audio_radio import RedAudioRadio


async def setup(bot):
    await bot.add_cog(RedAudioRadio(bot))